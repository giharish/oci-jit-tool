-- Adds start-time scheduling support and keeps provisioning to existing IAM users only.
--
-- Usage:
--   psql "<dsn>" -v jit_schema=customer_jit_access \
--     -f database/postgresql_migration_006_existing_users_start_time.sql

\set ON_ERROR_STOP on

begin;

alter table :"jit_schema".access_request
  add column if not exists requested_start_at timestamptz;

update :"jit_schema".access_request
set requested_start_at = created_at
where requested_start_at is null;

alter table :"jit_schema".access_request
  drop constraint if exists access_request_status_check;

alter table :"jit_schema".access_request
  add constraint access_request_status_check
  check (status in ('requested', 'scheduled', 'active', 'rejected', 'revoked', 'failed', 'revoke_failed'));

commit;
