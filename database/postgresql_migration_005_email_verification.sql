-- Migration 005: restrict self-service registration with email verification tokens.
--
-- Usage:
--   psql "host=<postgres-host> port=5432 dbname=<existing-db> user=<admin-user> sslmode=verify-full sslrootcert=<path-to-dbsystem.pub>" \
--     -v jit_schema=customer_jit_access \
--     -f database/postgresql_migration_005_email_verification.sql

\set ON_ERROR_STOP on

\if :{?jit_schema}
\else
  \set jit_schema customer_jit_access
\endif

begin;

alter table :"jit_schema".app_user
  add column if not exists verification_token text,
  add column if not exists verification_sent_at timestamptz,
  add column if not exists verified_at timestamptz;

alter table :"jit_schema".app_user
  drop constraint if exists app_user_status_check;

alter table :"jit_schema".app_user
  add constraint app_user_status_check
  check (status in ('pending_verification', 'active', 'disabled'));

update :"jit_schema".app_user
set verified_at = coalesce(verified_at, created_at)
where status = 'active'
  and auth_source = 'builtin'
  and verified_at is null;

commit;
