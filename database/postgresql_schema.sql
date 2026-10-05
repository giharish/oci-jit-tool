-- OCI IAM JIT Access PostgreSQL schema.
--
-- Usage:
--   psql "host=<postgres-host> port=5432 dbname=<existing-db> user=<admin-user> sslmode=require" \
--     -v jit_schema=jit_access \
--     -v app_role=jit_access_app \
--     -f database/postgresql_schema.sql
--
-- The schema and role names are intentionally supplied as psql variables so this
-- app can live in a separate schema inside an existing OCI PostgreSQL database.

\set ON_ERROR_STOP on

begin;

create schema if not exists :"jit_schema";

grant usage on schema :"jit_schema" to :"app_role";
alter default privileges in schema :"jit_schema" grant select, insert, update, delete on tables to :"app_role";
alter default privileges in schema :"jit_schema" grant usage, select on sequences to :"app_role";

create table if not exists :"jit_schema".tenancy_catalog (
  tenancy_ocid text primary key,
  display_name text not null,
  is_parent boolean not null,
  lifecycle_state text not null,
  home_region text,
  last_seen_at timestamptz not null,
  catalog_status text not null
);

create table if not exists :"jit_schema".group_catalog (
  group_ocid text primary key,
  group_name text not null,
  tenancy_ocid text not null references :"jit_schema".tenancy_catalog(tenancy_ocid) on delete cascade,
  description text,
  access_template text,
  policy_summary text,
  is_default boolean not null default false,
  active boolean not null default true,
  last_seen_at timestamptz not null
);

create table if not exists :"jit_schema".app_user (
  email text primary key,
  display_name text not null,
  password_hash text,
  auth_source text not null check (auth_source in ('builtin', 'idp')),
  role text not null default 'requester' check (role in ('requester', 'admin')),
  status text not null default 'active' check (status in ('pending_verification', 'active', 'disabled')),
  verification_token text,
  verification_sent_at timestamptz,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  last_login_at timestamptz
);

create table if not exists :"jit_schema".access_request (
  request_id bigint generated always as identity primary key,
  requester_email text not null,
  requester_name text not null,
  target_tenancy_ocid text not null references :"jit_schema".tenancy_catalog(tenancy_ocid),
  target_tenancy_ocids jsonb not null default '[]'::jsonb,
  requested_start_at timestamptz,
  target_compartment_ocid text,
  access_type text not null check (access_type in ('standard_group', 'custom_policy')),
  group_ocid text,
  requested_group_name text,
  service_family text,
  verb text,
  duration_minutes integer not null check (duration_minutes > 0),
  justification text not null,
  generated_policy_statements jsonb not null default '[]'::jsonb,
  jira_issue_key text,
  status text not null check (status in ('requested', 'scheduled', 'active', 'rejected', 'revoked', 'failed', 'revoke_failed')),
  approved_by text,
  approved_at timestamptz,
  expires_at timestamptz,
  revoked_at timestamptz,
  extension_count integer not null default 0 check (extension_count >= 0),
  extension_status text check (extension_status is null or extension_status in ('extension_pending', 'extension_approved', 'extension_rejected')),
  extension_minutes integer check (extension_minutes is null or extension_minutes > 0),
  extension_requested_by text,
  extension_requested_at timestamptz,
  extension_approved_by text,
  extension_approved_at timestamptz,
  extension_token text,
  provisioned_memberships jsonb not null default '[]'::jsonb,
  revocation_errors jsonb not null default '[]'::jsonb,
  warning_sent_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists :"jit_schema".audit_event (
  event_id bigint generated always as identity primary key,
  request_id bigint references :"jit_schema".access_request(request_id) on delete set null,
  actor text,
  action text not null,
  target_tenancy_ocid text,
  payload_json jsonb not null default '{}'::jsonb,
  result text not null,
  created_at timestamptz not null default now()
);

create index if not exists group_catalog_tenancy_idx
  on :"jit_schema".group_catalog(tenancy_ocid, active, group_name);

create index if not exists app_user_status_idx
  on :"jit_schema".app_user(status, auth_source);

create index if not exists access_request_requester_idx
  on :"jit_schema".access_request(requester_email, created_at desc);

create index if not exists access_request_status_expiry_idx
  on :"jit_schema".access_request(status, expires_at)
  where expires_at is not null;

create index if not exists audit_event_request_idx
  on :"jit_schema".audit_event(request_id, created_at desc);

create index if not exists audit_event_created_idx
  on :"jit_schema".audit_event(created_at desc);

grant select, insert, update, delete on all tables in schema :"jit_schema" to :"app_role";
grant usage, select on all sequences in schema :"jit_schema" to :"app_role";

commit;
