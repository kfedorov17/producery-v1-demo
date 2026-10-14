-- Demo book of business for "Lakeview Insurance Group" (fictional). Idempotent.
create table if not exists households (
  id            text primary key,
  display_name  text not null,
  segment       text not null check (segment in ('monoline','bundled','commercial_small')),
  email         text,
  risk_flags    text[] default '{}',
  annual_premium numeric,
  last_touch_at timestamptz default now()
);

create table if not exists policies (
  id            text primary key,
  household_id  text references households(id),
  line          text not null,
  status        text not null default 'active',
  policy_number text unique,
  premium       numeric,
  prior_term_premium numeric,
  x_date        date,
  carrier       text
);

create table if not exists appointments (
  id            text primary key,
  carrier_name  text not null,
  status        text not null check (status in ('researching','applied','pending_docs','pending_review','active','at_risk','terminated')),
  applied_date  date,
  active_date   date
);

insert into households (id, display_name, segment, email, risk_flags, annual_premium) values
  ('hh-1', 'Maria Gonzalez',      'bundled',          'maria@example.net',          '{teen_driver}', 2660),
  ('hh-2', 'Wei Chen',            'monoline',         'wei@example.net',            '{}',            980),
  ('hh-3', 'Sam Patel',           'bundled',          'sam@example.net',            '{older_roof}',  3570),
  ('hh-4', 'Bright Oven Bakery',  'commercial_small', 'dana@brightoven.example',    '{}',            3200),
  ('hh-5', 'Aisha Thompson',      'monoline',         'aisha@example.net',          '{pool}',        1310),
  ('hh-6', 'Moreau Catering LLC', 'commercial_small', 'wei.moreau@example.net',     '{}',            4812)
on conflict (id) do nothing;

insert into policies (id, household_id, line, policy_number, premium, prior_term_premium, x_date, carrier) values
  ('pol-1', 'hh-3', 'homeowners',      'HO-1001',     2150, 1800, current_date + 45,  'Granite Shield'),
  ('pol-2', 'hh-3', 'auto',            'AU-2001',     1420, 1380, current_date + 70,  'Granite Shield'),
  ('pol-3', 'hh-1', 'homeowners',      'HO-1002',     1680, 1640, current_date + 20,  'Keystone Mutual'),
  ('pol-4', 'hh-2', 'auto',            'AU-2002',      980,  990, current_date + 200, 'Lakeshore Casualty'),
  ('pol-5', 'hh-4', 'bop',             'BP-3001',     3200, 2700, current_date + 15,  'Harbor Specialty'),
  ('pol-6', 'hh-5', 'homeowners',      'HO-1003',     1310, 1300, current_date + 120, 'Keystone Mutual'),
  ('pol-7', 'hh-6', 'commercial auto', 'CA-88214-07', 4812, 4100, current_date + 90,  'Granite Shield'),
  ('pol-8', 'hh-1', 'auto',            'AU-2003',      980, 1020, current_date + 20,  'Keystone Mutual')
on conflict (id) do nothing;

insert into appointments (id, carrier_name, status, applied_date, active_date) values
  ('ap-1', 'Granite Shield',     'active',       current_date - 200, current_date - 150),
  ('ap-2', 'Keystone Mutual',    'active',       current_date - 300, current_date - 250),
  ('ap-3', 'Harbor Specialty',   'pending_docs', current_date - 20,  null),
  ('ap-4', 'Lakeshore Casualty', 'active',       current_date - 100, current_date - 60)
on conflict (id) do nothing;

-- Allowlisted read views — the ONLY surface sql_query exposes. Base tables are never granted.
create or replace view v_households as
  select h.display_name, h.segment, h.risk_flags, h.annual_premium,
         (select count(*) from policies p where p.household_id = h.id) as policy_count
  from households h;

create or replace view v_policies as
  select h.display_name as household_name, p.line, p.status, p.x_date,
         p.premium, p.prior_term_premium, p.policy_number, p.carrier
  from policies p join households h on h.id = p.household_id;

create or replace view v_renewals as
  select h.display_name as household_name, p.line, p.policy_number, p.x_date, p.premium,
         p.prior_term_premium,
         round(100.0 * (p.premium - p.prior_term_premium) / nullif(p.prior_term_premium, 0), 1) as premium_change_pct,
         (p.premium - p.prior_term_premium) / nullif(p.prior_term_premium, 0) >= 0.15 as remarket_flag
  from policies p join households h on h.id = p.household_id
  where p.x_date <= current_date + 120;

create or replace view v_appointments as
  select carrier_name, status, applied_date, active_date from appointments;

grant select on v_households, v_policies, v_renewals, v_appointments to producery_readonly;
grant select on documents, document_chunks to producery_readonly;  -- documents_search also runs read-only
