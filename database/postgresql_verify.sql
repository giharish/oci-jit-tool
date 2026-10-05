-- Verify the OCI IAM JIT Access PostgreSQL schema and runtime grants.
--
-- Usage:
--   psql "host=<postgres-host> port=5432 dbname=<existing-db> user=<admin-user> sslmode=require" \
--     -v jit_schema=jit_access \
--     -v app_role=jit_access_app \
--     -f database/postgresql_verify.sql

\set ON_ERROR_STOP on

\if :{?jit_schema}
\else
  \set jit_schema customer_jit_access
\endif

\if :{?app_role}
\else
  \set app_role jit_access_app
\endif

select
  current_database() as connected_database,
  current_user as connected_user,
  inet_server_addr() as server_address,
  inet_server_port() as server_port;

select
  nspname as schema_name,
  pg_get_userbyid(nspowner) as schema_owner
from pg_namespace
where nspname = :'jit_schema';

select
  n.nspname as table_schema,
  c.relname as table_name,
  case c.relkind
    when 'r' then 'table'
    when 'i' then 'index'
    when 'S' then 'sequence'
    else c.relkind::text
  end as object_type,
  pg_get_userbyid(c.relowner) as owner
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = :'jit_schema'
  and c.relkind in ('r', 'i', 'S')
order by object_type, table_name;

select
  rolname as runtime_role,
  rolcanlogin as can_login
from pg_roles
where rolname = :'app_role';

select
  table_schema,
  table_name,
  privilege_type
from information_schema.role_table_grants
where grantee = :'app_role'
  and table_schema = :'jit_schema'
order by table_name, privilege_type;

select
  table_schema,
  table_name,
  column_name,
  data_type,
  is_nullable
from information_schema.columns
where table_schema = :'jit_schema'
  and table_name = 'access_request'
  and column_name in (
    'target_tenancy_ocids',
    'provisioned_memberships',
    'revocation_errors'
  )
order by column_name;
