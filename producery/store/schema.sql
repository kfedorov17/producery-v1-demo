-- Producery application schema. Idempotent: safe to re-apply on every seed run.
-- Checkpoints (langgraph checkpointer) and the long-term store (langgraph store) create their own tables.

create extension if not exists vector;

-- roles -----------------------------------------------------------------------
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'producery_readonly') then
    create role producery_readonly nologin;
  end if;
end $$;

-- inbox ------------------------------------------------------------------------
create table if not exists inbox (
  id            text primary key,                 -- stable fixture/email id → thread_id = crew:<id>
  from_email    text not null,
  from_name     text,
  subject       text,
  body          text not null,
  attachment_document_id text,
  status        text not null default 'held'      -- held | new | running | paused | done | failed
    check (status in ('held','new','running','paused','done','failed')),
  thread_id     text,
  received_at   timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists inbox_status_received_idx on inbox (status, received_at);  -- poll_inbox / UI partitions

-- documents + chunks (semantic search) ------------------------------------------
create table if not exists documents (
  id            text primary key,
  title         text not null,
  doc_type      text not null check (doc_type in ('contract','dec_page','help','other')),
  body          text not null,
  content_hash  text not null,
  status        text not null default 'pending' check (status in ('pending','ingesting','ready','failed')),
  chunk_count   int  not null default 0,
  created_at    timestamptz not null default now(),
  ingested_at   timestamptz
);

create table if not exists document_chunks (
  id            text primary key,                 -- <doc_id>#<ord>
  document_id   text not null references documents(id) on delete cascade,
  ord           int  not null,
  section       text,
  content       text not null,
  embedding     vector(384),
  tsv           tsvector generated always as (to_tsvector('english', content)) stored
);
create index if not exists document_chunks_tsv_idx on document_chunks using gin (tsv);
create index if not exists document_chunks_doc_idx on document_chunks (document_id, ord);

-- proposals (every consequential action lands here before a human decides) -----
create table if not exists proposals (
  id            text primary key,
  thread_id     text not null unique,              -- one proposal per crew thread (idempotent staging)
  kind          text not null check (kind in ('send_email','create_task','update_record')),
  payload       jsonb not null,
  owner_brief   text,
  compliance_flags jsonb not null default '[]',
  status        text not null default 'pending',   -- check constraint maintained below (idempotent swap)
  decided_by    text,
  decision_reason text,
  created_at    timestamptz not null default now(),
  decided_at    timestamptz,
  executed_at   timestamptz
);

-- pending | approved | edited | rejected | executed | expired (checkpoint pruned before a decision; nothing executes)
alter table proposals drop constraint if exists proposals_status_check;
alter table proposals add constraint proposals_status_check
  check (status in ('pending','approved','edited','rejected','executed','expired'));

create table if not exists outbox (
  id            bigserial primary key,
  proposal_id   text not null references proposals(id),
  to_email      text not null,
  subject       text,
  body          text,
  sent_at       timestamptz not null default now()
);

create unique index if not exists outbox_proposal_uq on outbox (proposal_id);  -- one outbound email per proposal, ever

create table if not exists run_claims (
  thread_id     text primary key,                 -- held while an agent instance runs the thread (stale after 15 min)
  claimed_at    timestamptz not null default now()
);

create table if not exists ui_connections (
  provider      text primary key check (provider in ('email','onedrive','gdrive')),
  status        text not null default 'manual' check (status in ('manual','connecting','connected')),
  steps_done    jsonb not null default '[]',   -- checklist steps the producer ticked (UI state; the crew never reads it)
  updated_at    timestamptz not null default now()
);

create table if not exists audit_log (
  id            bigserial primary key,
  at            timestamptz not null default now(),
  thread_id     text,
  actor         text not null,
  action        text not null,
  ref           jsonb
);
create index if not exists audit_log_thread_idx on audit_log (thread_id, id);  -- Thread page / history reads

-- least privilege: the readonly role has SELECT on the allowlisted views and on documents/document_chunks (fixtures/book.sql)
grant usage on schema public to producery_readonly;
