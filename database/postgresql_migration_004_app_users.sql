-- Migration 004: built-in portal users and self-service requester accounts.
--
-- Usage:
--   psql "host=<postgres-host> port=5432 dbname=<existing-db> user=<admin-user> sslmode=verify-full sslrootcert=<path-to-dbsystem.pub>" \
--     -v jit_schema=customer_jit_access \
--     -v app_role=jit_access_app \
--     -f database/postgresql_migration_004_app_users.sql

\set ON_ERROR_STOP on

\if :{?jit_schema}
\else
  \set jit_schema customer_jit_access
\endif

\if :{?app_role}
\else
  \set app_role jit_access_app
\endif

begin;

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

create index if not exists app_user_status_idx
  on :"jit_schema".app_user(status, auth_source);

grant select, insert, update, delete on :"jit_schema".app_user to :"app_role";

commit;
