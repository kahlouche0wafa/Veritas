-- VERITAS — Supabase / Postgres schema
--
-- Apply once from the Supabase SQL editor (or `psql`) before running
-- scripts/seed_demo.py or starting the API.
--
-- IDs are TEXT so they line up with uuid.uuid4().hex in the Python models
-- (32-char hex, no dashes). Feel free to switch to `uuid` types if you'd
-- rather Postgres generate them — the Python side already sends the id.

create table if not exists claims (
    id                  text primary key,
    subject_id          text not null,
    phone               text not null,
    assertion_text      text not null,
    claimed_site_lat    double precision not null,
    claimed_site_lon    double precision not null,
    claimed_radius_m    double precision not null,
    claimed_site_label  text,
    time_window_start   timestamptz not null,
    time_window_end     timestamptz not null,
    source              text not null default 'manual',
    consent_status      text not null default 'ACTIVE',
    created_at          timestamptz not null default now()
);

create index if not exists claims_subject_id_idx    on claims (subject_id);
create index if not exists claims_created_at_desc   on claims (created_at desc);

create table if not exists evidence_records (
    id              text primary key,
    claim_id        text not null references claims(id) on delete cascade,
    profile         text not null,
    verdict         text not null,
    confidence      text not null,
    evidence        jsonb not null,
    rationale       text not null,
    consent_status  text not null,
    liveness        jsonb,
    is_seed         boolean not null default false,   -- flags synthetic demo history
    created_at      timestamptz not null default now()
);

create index if not exists evidence_records_claim_id_idx    on evidence_records (claim_id);
create index if not exists evidence_records_created_at_desc on evidence_records (created_at desc);
create index if not exists evidence_records_verdict_idx     on evidence_records (verdict);
