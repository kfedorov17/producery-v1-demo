"""Transactional records: inbox, proposals, outbox, audit, artifacts, documents. Every write is idempotent."""

from __future__ import annotations

import hashlib
from typing import Any

from producery.store.db import Database, jsonb


class Records:
    """Idempotent SQL access to the inbox, documents, proposals, run claims, UI tables and audit log."""

    def __init__(self, db: Database) -> None:
        """Bind the records to a database.

        Args:
            db: The pool wrapper every statement runs through.
        """
        self.db = db

    # ---- inbox -------------------------------------------------------------
    async def upsert_email(self, email: dict[str, Any], status: str = "held") -> None:
        """Insert an inbox row; an existing id is left untouched (``on conflict do nothing``).

        Args:
            email: Row fields id, from_email, from_name, subject, body and attachment_document_id.
            status: Initial inbox status.
        """
        await self.db.execute(
            """insert into inbox (id, from_email, from_name, subject, body, attachment_document_id, status)
               values (%(id)s, %(from_email)s, %(from_name)s, %(subject)s, %(body)s, %(attachment_document_id)s, %(status)s)
               on conflict (id) do nothing""",
            {**email, "status": status},
        )

    async def get_email(self, email_id: str) -> dict[str, Any] | None:
        """Return the inbox row for an id, or None.

        Args:
            email_id: The inbox row id.
        """
        return await self.db.fetchone("select * from inbox where id = %s", (email_id,))

    async def claim_new_emails(self, limit: int = 10) -> list[dict[str, Any]]:
        """Move up to ``limit`` ``new`` rows to ``running`` and return them (``for update skip locked``).

        Args:
            limit: Maximum rows claimed, oldest ``received_at`` first.
        """
        return await self.db.fetch(
            """update inbox set status='running', updated_at=now()
               where id in (select id from inbox where status='new' order by received_at limit %s for update skip locked)
               returning *""",
            (limit,),
        )

    async def set_email_status(self, email_id: str, status: str, thread_id: str | None = None) -> None:
        """Update an inbox row's status; the thread id is set only when given (``coalesce``).

        Args:
            email_id: The inbox row id.
            status: New status.
            thread_id: Thread to record on the row, or None to keep the existing value.
        """
        await self.db.execute(
            "update inbox set status=%s, thread_id=coalesce(%s, thread_id), updated_at=now() where id=%s",
            (status, thread_id, email_id),
        )

    # ---- documents ----------------------------------------------------------
    async def upsert_document(self, doc_id: str, title: str, doc_type: str, body: str) -> bool:
        """Insert or replace a document as ``pending`` unless the same content is already ``ready``.

        Args:
            doc_id: Document id.
            title: Document title.
            doc_type: Document type.
            body: Full text; its SHA-256 is stored as ``content_hash``.

        Returns:
            True when the row was written and needs ingestion, False when it was already ready with this
            content.
        """
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()
        row = await self.db.fetchone("select content_hash, status from documents where id=%s", (doc_id,))
        if row and row["content_hash"] == h and row["status"] == "ready":
            return False
        await self.db.execute(
            """insert into documents (id, title, doc_type, body, content_hash, status)
               values (%s,%s,%s,%s,%s,'pending')
               on conflict (id) do update set title=excluded.title, doc_type=excluded.doc_type, body=excluded.body,
                 content_hash=excluded.content_hash, status='pending', chunk_count=0, ingested_at=null""",
            (doc_id, title, doc_type, body, h),
        )
        return True

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """Return id, title, doc_type, body and status of a document, or None.

        Args:
            doc_id: Document id.
        """
        return await self.db.fetchone(
            "select id, title, doc_type, body, status from documents where id=%s", (doc_id,)
        )

    async def replace_chunks(self, doc_id: str, chunks: list[dict[str, Any]]) -> None:
        """Replace a document's chunks in one transaction and mark it ``ready``.

        The document goes to ``ingesting``, its old chunks are deleted, the new ones are inserted with their
        embeddings, then status, ``chunk_count`` and ``ingested_at`` are set.

        Args:
            doc_id: Document id.
            chunks: Dicts with ord, content, embedding and an optional section.
        """
        async with self.db.connection() as conn:
            async with conn.transaction():
                await conn.execute("update documents set status='ingesting' where id=%s", (doc_id,))
                await conn.execute("delete from document_chunks where document_id=%s", (doc_id,))
                for c in chunks:
                    await conn.execute(
                        """insert into document_chunks (id, document_id, ord, section, content, embedding)
                           values (%s,%s,%s,%s,%s,%s::vector)""",
                        (
                            f"{doc_id}#{c['ord']}",
                            doc_id,
                            c["ord"],
                            c.get("section"),
                            c["content"],
                            c["embedding"],
                        ),
                    )
                await conn.execute(
                    "update documents set status='ready', chunk_count=%s, ingested_at=now() where id=%s",
                    (len(chunks), doc_id),
                )

    # ---- proposals -----------------------------------------------------------
    async def stage_proposal(
        self, thread_id: str, kind: str, payload: dict[str, Any], owner_brief: str, flags: list[dict]
    ) -> str:
        """Insert a proposal for a thread, or update it while it is still ``pending``.

        The id is derived from the thread id, so re-staging the same thread keeps one row; a proposal that is
        no longer pending is left unchanged.

        Args:
            thread_id: Thread the proposal belongs to.
            kind: Proposal kind (for example ``send_email``).
            payload: Proposal payload, stored as jsonb.
            owner_brief: Text shown to the owner.
            flags: Compliance flags, stored as jsonb.

        Returns:
            The proposal id.
        """
        pid = f"prop-{hashlib.sha1(thread_id.encode()).hexdigest()[:12]}"  # noqa: S324 — id derivation, not security
        await self.db.execute(
            """insert into proposals (id, thread_id, kind, payload, owner_brief, compliance_flags)
               values (%s,%s,%s,%s::jsonb,%s,%s::jsonb)
               on conflict (thread_id) do update set kind=excluded.kind, payload=excluded.payload,
                 owner_brief=excluded.owner_brief, compliance_flags=excluded.compliance_flags
               where proposals.status = 'pending'""",
            (pid, thread_id, kind, jsonb(payload), owner_brief, jsonb(flags)),
        )
        return pid

    async def expire_pending_proposal(self, thread_id: str, reason: str) -> str | None:
        """Expire a pruned thread's pending proposal and return its id, or None when nothing was pending.

        A thread whose checkpoint was pruned cannot resume, so its pending proposal can never be decided: it
        is flipped to `expired` (never executed) with ``decided_by='retention'``.

        Args:
            thread_id: Thread whose pending proposal is expired.
            reason: Stored as the decision reason.
        """
        row = await self.db.fetchone(
            """update proposals set status='expired', decided_by='retention', decision_reason=%s, decided_at=now()
               where thread_id=%s and status='pending' returning id""",
            (reason, thread_id),
        )
        return (row or {}).get("id")

    async def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Return the proposal row for an id, or None.

        Args:
            proposal_id: Proposal id.
        """
        return await self.db.fetchone("select * from proposals where id=%s", (proposal_id,))

    async def decide_proposal(
        self, proposal_id: str, status: str, by: str, reason: str | None, payload: dict | None
    ) -> None:
        """Record a decision on a proposal that is still ``pending``; other statuses are left unchanged.

        Args:
            proposal_id: Proposal id.
            status: New status.
            by: Who decided.
            reason: Decision reason, if any.
            payload: Replacement payload (an edited draft), or None to keep the stored one.
        """
        if payload is not None:
            await self.db.execute(
                """update proposals set status=%s, decided_by=%s, decision_reason=%s, payload=%s::jsonb, decided_at=now()
                   where id=%s and status='pending'""",
                (status, by, reason, jsonb(payload), proposal_id),
            )
        else:
            await self.db.execute(
                "update proposals set status=%s, decided_by=%s, decision_reason=%s, decided_at=now() where id=%s and status='pending'",
                (status, by, reason, proposal_id),
            )

    async def execute_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Execute a proposal exactly once and return its row, or None when the id is unknown.

        Idempotent and atomic: the row is locked, the outbox insert and the status flip commit together, and a
        replay sees status='executed' (or hits the unique outbox index) and returns the row unchanged. Only
        ``send_email`` proposals write an outbox row.

        Args:
            proposal_id: Proposal id.
        """
        async with self.db.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute("select * from proposals where id=%s for update", (proposal_id,))
                row = await cur.fetchone()
                if row is None:
                    return None
                if row["status"] == "executed":
                    return dict(row)
                if row["kind"] == "send_email":
                    p = row["payload"]
                    await conn.execute(
                        """insert into outbox (proposal_id, to_email, subject, body) values (%s,%s,%s,%s)
                           on conflict (proposal_id) do nothing""",
                        (proposal_id, p.get("to", ""), p.get("subject"), p.get("body")),
                    )
                await conn.execute(
                    "update proposals set status='executed', executed_at=now() where id=%s", (proposal_id,)
                )
        return await self.get_proposal(proposal_id)

    # ---- run claims (one run per thread across agent instances) -----------------
    async def claim_thread(self, thread_id: str) -> bool:
        """Claim a thread for one run; a claim older than 15 minutes is taken over.

        Args:
            thread_id: Thread to claim.

        Returns:
            True when the claim was obtained, False when another live claim exists.
        """
        rows = await self.db.fetch(
            """insert into run_claims (thread_id) values (%s)
               on conflict (thread_id) do update set claimed_at = now()
               where run_claims.claimed_at < now() - interval '15 minutes'
               returning thread_id""",
            (thread_id,),
        )
        return bool(rows)

    async def release_thread(self, thread_id: str) -> None:
        """Drop the run claim for a thread.

        Args:
            thread_id: Thread to release.
        """
        await self.db.execute("delete from run_claims where thread_id=%s", (thread_id,))

    # ---- producer UI reads (docs/design.md §14) --------------------------------------
    async def list_inbox(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        """Return inbox rows joined with their proposal, most recently updated first.

        Args:
            status: Inbox status filter, or None for all.
            limit: Maximum rows.
        """
        where = "where i.status = %s" if status else ""
        return await self.db.fetch(
            f"""select i.id, i.from_email, i.from_name, i.subject, i.status, i.thread_id, i.received_at, i.updated_at,
                      p.kind as proposal_kind, p.status as proposal_status, p.id as proposal_id
               from inbox i left join proposals p on p.thread_id = i.thread_id
               {where} order by i.updated_at desc limit %s""",
            (status, limit) if status else (limit,),
        )

    async def list_proposals(
        self, status: str | None, limit: int, thread_class: str = "crew"
    ) -> list[dict[str, Any]]:
        """Return proposals of one thread class joined with their inbox row, newest first.

        Args:
            status: Proposal status filter, or None for all.
            limit: Maximum rows.
            thread_class: Thread id prefix to match (``<thread_class>:%``).
        """
        where = "where p.thread_id like %s" + (" and p.status = %s" if status else "")
        return await self.db.fetch(
            f"""select p.id, p.thread_id, p.kind, p.payload, p.owner_brief, p.compliance_flags, p.status,
                      p.decided_by, p.decision_reason, p.created_at, p.decided_at, p.executed_at,
                      i.id as email_id, i.from_email, i.from_name, i.subject, i.received_at
               from proposals p left join inbox i on i.thread_id = p.thread_id
               {where} order by p.created_at desc limit %s""",
            (f"{thread_class}:%", status, limit) if status else (f"{thread_class}:%", limit),
        )

    async def audit_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        """Return the audit rows of a thread in insertion order.

        Args:
            thread_id: The thread id.
        """
        return await self.db.fetch(
            "select id, at, actor, action, ref from audit_log where thread_id = %s order by id", (thread_id,)
        )

    async def list_outbox(self, limit: int) -> list[dict[str, Any]]:
        """Return outbox rows with their proposal's thread id, newest first.

        Args:
            limit: Maximum rows.
        """
        return await self.db.fetch(
            """select o.id, o.proposal_id, o.to_email, o.subject, o.body, o.sent_at, p.thread_id
               from outbox o join proposals p on p.id = o.proposal_id order by o.id desc limit %s""",
            (limit,),
        )

    async def list_documents(self) -> list[dict[str, Any]]:
        """Return every document's metadata (no body), newest first."""
        return await self.db.fetch(
            "select id, title, doc_type, status, chunk_count, created_at, ingested_at from documents order by created_at desc"
        )

    async def get_connections(self) -> list[dict[str, Any]]:
        """Return the stored connection checklist rows."""
        return await self.db.fetch("select provider, status, steps_done, updated_at from ui_connections")

    async def put_connection(self, provider: str, status: str, steps_done: list[str]) -> dict[str, Any]:
        """Insert or update a provider's connection row and return it.

        Args:
            provider: Provider name.
            status: Connection status.
            steps_done: Checklist steps, stored as jsonb.
        """
        rows = await self.db.fetch(
            """insert into ui_connections (provider, status, steps_done, updated_at) values (%s, %s, %s::jsonb, now())
               on conflict (provider) do update set status = excluded.status, steps_done = excluded.steps_done, updated_at = now()
               returning provider, status, steps_done, updated_at""",
            (provider, status, jsonb(steps_done)),
        )
        return rows[0]

    # ---- audit -------------------------------------------------------------------
    async def audit(self, thread_id: str, actor: str, action: str, ref: dict[str, Any]) -> None:
        """Append an audit row.

        Args:
            thread_id: Thread (or logical scope such as ``worker``) the entry belongs to.
            actor: Who acted.
            action: Action name.
            ref: Details, stored as jsonb.
        """
        await self.db.execute(
            "insert into audit_log (thread_id, actor, action, ref) values (%s,%s,%s,%s::jsonb)",
            (thread_id, actor, action, jsonb(ref)),
        )
