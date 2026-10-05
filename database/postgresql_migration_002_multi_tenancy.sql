-- Migration 002: multi-tenancy grants, predefined groups only, and retryable revocation failures.
--
-- Usage:
--   psql "host=<postgres-host> port=5432 dbname=<existing-db> user=<admin-user> sslmode=require" \
--     -v jit_schema=customer_jit_access \
--     -f database/postgresql_migration_002_multi_tenancy.sql

\set ON_ERROR_STOP on

\if :{?jit_schema}
\else
  \set jit_schema customer_jit_access
\endif

begin;

alter table :"jit_schema".access_request
  add column if not exists target_tenancy_ocids jsonb not null default '[]'::jsonb,
  add column if not exists provisioned_memberships jsonb not null default '[]'::jsonb,
  add column if not exists revocation_errors jsonb not null default '[]'::jsonb;

update :"jit_schema".access_request
set target_tenancy_ocids = jsonb_build_array(target_tenancy_ocid)
where target_tenancy_ocids = '[]'::jsonb
  and target_tenancy_ocid is not null;

alter table :"jit_schema".access_request
  drop constraint if exists access_request_status_check;

alter table :"jit_schema".access_request
  add constraint access_request_status_check
  check (status in ('requested', 'active', 'rejected', 'revoked', 'failed', 'revoke_failed'));

alter table :"jit_schema".access_request
  drop constraint if exists access_request_extension_count_check;

alter table :"jit_schema".access_request
  add constraint access_request_extension_count_check
  check (extension_count >= 0);

commit;
