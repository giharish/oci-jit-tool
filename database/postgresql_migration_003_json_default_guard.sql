-- Migration 003: protect JSON tracking columns from older function images that send NULL.
--
-- Usage:
--   psql "host=<postgres-host> port=5432 dbname=<existing-db> user=<admin-user> sslmode=verify-full sslrootcert=<path-to-dbsystem.pub>" \
--     -v jit_schema=customer_jit_access \
--     -f database/postgresql_migration_003_json_default_guard.sql

\set ON_ERROR_STOP on

\if :{?jit_schema}
\else
  \set jit_schema customer_jit_access
\endif

begin;

update :"jit_schema".access_request
set
  target_tenancy_ocids = coalesce(target_tenancy_ocids, '[]'::jsonb),
  provisioned_memberships = coalesce(provisioned_memberships, '[]'::jsonb),
  revocation_errors = coalesce(revocation_errors, '[]'::jsonb);

create or replace function :"jit_schema".coalesce_access_request_json_defaults()
returns trigger
language plpgsql
as $$
begin
  new.target_tenancy_ocids = coalesce(new.target_tenancy_ocids, '[]'::jsonb);
  new.provisioned_memberships = coalesce(new.provisioned_memberships, '[]'::jsonb);
  new.revocation_errors = coalesce(new.revocation_errors, '[]'::jsonb);
  return new;
end;
$$;

drop trigger if exists access_request_json_defaults_guard on :"jit_schema".access_request;

create trigger access_request_json_defaults_guard
before insert or update on :"jit_schema".access_request
for each row
execute function :"jit_schema".coalesce_access_request_json_defaults();

commit;
