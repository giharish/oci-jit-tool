# OCI IAM JIT Access Production App Scaffold

This generated application aligns to the production requirements:

- Frontend portal calls backend APIs that map one-to-one to OCI Functions.
- Catalog sync discovers the parent tenancy and child tenancies instead of hardcoding the request form.
- Default JIT groups are discovered per tenancy and shown as dropdown options.
- Access requests use Terraform-managed predefined JIT groups/policies; dynamic policy creation is disabled by default.
- Approval, provisioning, revocation, expiry cleanup, Jira integration boundary, and audit evidence are separated.
- Active requests show a countdown timer.
- Warning email events are sent when less than 15 minutes remain.
- Requesters can ask for up to two extensions, each capped at the originally requested duration.

## Project Layout

```text
jit-production-app/
  frontend/                  Static portal
  app/                       Shared backend logic used by all functions
  functions/                 OCI Functions-style entrypoints
  database/                  OCI PostgreSQL schema scripts
  deployment/                Function app config and build/deploy helpers
  local_server.py            Local API Gateway simulator
  terraform/                 API Gateway / Functions deployment skeleton
  tests/                     Local lifecycle tests
```

## Run Locally

```bash
cd /Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app
python3 local_server.py
```

Open:

```text
http://127.0.0.1:8090
```

Demo flow:

1. Start local admin mode with `JIT_LOCAL_AUTH_EMAIL=iam.admin@customer.example python3 local_server.py`.
2. Open `Catalog` and click `Sync Catalog`.
3. Restart anonymous requester mode with `python3 local_server.py`.
4. Open `Request`, select a child tenancy and one of its discovered JIT groups, then submit a request.
5. Restart local admin mode with `JIT_LOCAL_AUTH_EMAIL=iam.admin@customer.example python3 local_server.py`.
6. Open `Approval` and approve the request.
7. Watch the active request countdown.
8. Run the warning worker when less than 15 minutes remain to simulate email notification.
9. Restart anonymous requester mode and request an extension from `My Access`.
10. Restart local admin mode and approve the extension.
11. Revoke manually or run expiry worker.
12. Open `Audit` as the configured admin to show evidence.

For local API testing, you can also inject a trusted identity with the `X-Authenticated-User` or `X-Forwarded-Email` header. In production, these headers must be set only by API Gateway/OIDC/IAM middleware and stripped from inbound client traffic.

## Production Function Mapping

| Portal/API action | Function |
|---|---|
| Built-in login and session validation | `auth-session-fn` |
| `GET /api/catalog` and catalog refresh | `catalog-sync-fn` |
| `POST /api/requests` | `request-access-fn` |
| Jira approval webhook | `approval-callback-fn` |
| Manual provision/retry | `provision-access-fn` |
| Manual revoke | `revoke-access-fn` |
| Custom policy preview | `policy-builder-fn` |
| Request extension | `request-extension-fn` |
| Approve/reject extension | `approve-extension-fn` |
| Expiry warning email | `notification-worker-fn` |
| Scheduled expiry cleanup | `expiry-scheduler-fn` |

## Current Connector Mode

The generated app supports two OCI connector modes:

- `JIT_CONNECTOR_MODE=mock` for local demos.
- `JIT_CONNECTOR_MODE=oci` for production OCI SDK calls.

Production OCI auth is selected with:

- `JIT_OCI_AUTH_MODE=resource_principal` for OCI Functions resource principal auth.
- `JIT_OCI_AUTH_MODE=api_key` for an OCI SDK config/profile, mainly for jump-host testing.

For single-tenancy testing, leave `JIT_ORGANIZATION_OCID` empty. With API-key auth, the app uses the tenancy from `JIT_OCI_CONFIG_FILE` and `JIT_OCI_PROFILE` unless `JIT_PARENT_TENANCY_OCID` is set. With resource-principal auth, set `JIT_PARENT_TENANCY_OCID` to the single target tenancy OCID.

For automatic parent/child tenancy discovery later, set `JIT_ORGANIZATION_OCID`. The app uses the OCI Tenant Manager Control Plane `OrganizationClient` to list organization tenancies and the Identity service to discover Terraform-managed JIT groups by `JIT_GROUP_PREFIX`.

The app does not create or delete JIT groups or policies in production. Terraform owns predefined groups and policies; the app only creates/fetches the requester user if needed and adds/removes that user from predefined JIT groups.

## Portal Authentication

The portal supports two authentication patterns:

- `JIT_PORTAL_AUTH_MODE=trusted_header` for production IdP integration through API Gateway/OIDC/IAM middleware. The gateway injects a trusted user header such as `X-Authenticated-User`; the app maps that email to `admin` only when it appears in `JIT_AUTHORIZED_USERS`.
- `JIT_PORTAL_AUTH_MODE=builtin` for portal-managed accounts. Users can self-register when `JIT_ALLOW_SELF_REGISTRATION=true`; account records are stored in `app_user`, passwords are PBKDF2-hashed, domains are restricted by `JIT_ALLOWED_EMAIL_DOMAINS`, and users must verify email tokens when `JIT_REQUIRE_EMAIL_VERIFICATION=true`.
- `JIT_PORTAL_AUTH_MODE=mixed` allows both built-in portal users and existing IdP/OIDC users. Existing IdP/AD users sign in through the customer-controlled gateway; built-in users sign in through the portal form.

For bootstrap admin accounts, generate a built-in password hash with:

```bash
python3 deployment/hash_builtin_password.py
```

Then configure:

```text
JIT_PORTAL_AUTH_MODE=builtin
JIT_ALLOW_SELF_REGISTRATION=true
JIT_ALLOW_ANONYMOUS_REQUESTS=false
JIT_ALLOWED_EMAIL_DOMAINS=customer.com,oracle.com
JIT_REQUIRE_EMAIL_VERIFICATION=true
JIT_PORTAL_BASE_URL=https://<gateway-host>/jit/
JIT_SESSION_SECRET=<long-random-secret>
JIT_BUILTIN_USERS={"admin.email@customer.com":{"password_hash":"pbkdf2_sha256$..."}}
JIT_AUTHORIZED_USERS={"admin.email@customer.com":"admin"}
```

## Production Database

Local runs use SQLite by default. Production is configured for OCI PostgreSQL with a dedicated schema inside an existing database:

- Create the schema and tables with [database/postgresql_schema.sql](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/database/postgresql_schema.sql).
- For existing deployments, run [database/postgresql_migration_004_app_users.sql](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/database/postgresql_migration_004_app_users.sql) to add the self-service account table, then [database/postgresql_migration_005_email_verification.sql](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/database/postgresql_migration_005_email_verification.sql) to add email verification fields.
- Verify schema objects and grants with [database/postgresql_verify.sql](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/database/postgresql_verify.sql).
- Set `JIT_DB_BACKEND=postgresql`.
- Set `JIT_POSTGRES_DSN` to the existing OCI PostgreSQL database FQDN with `sslmode=verify-full`.
- Set `JIT_POSTGRES_SCHEMA=customer_jit_access` or the customer-approved schema name.
- Provide the OCI PostgreSQL CA certificate through either `JIT_POSTGRES_SSLROOTCERT_PATH` or `JIT_POSTGRES_SSLROOTCERT_PEM`.
- Keep `JIT_AUTO_INIT_DB=false` in production so Functions do not need DDL privileges.
- Set `JIT_NOTIFICATION_MODE=smtp` to send verification, expiry warning, and revocation-failure emails through OCI Email Delivery or the customer's SMTP relay.

## Security Guardrails Implemented

- Requester self-approval is blocked.
- Browser payloads cannot choose the acting user.
- Authenticated requesters can submit access only for their own email address.
- Anonymous request creation and request lookup are disabled by default with `JIT_ALLOW_ANONYMOUS_REQUESTS=false`.
- Only configured `admin` users in `JIT_AUTHORIZED_USERS` can approve, reject, revoke, view all requests, sync catalog, or run worker actions from the portal.
- Non-admin users can view only requests matching their own authenticated email.
- Dynamic custom policies are disabled by default with `JIT_ALLOW_CUSTOM_POLICIES=false`.
- Terraform owns predefined JIT groups and policies.
- The app only adds/removes existing OCI IAM users from discovered JIT groups; it does not create OCI IAM users.
- Single- and multi-tenancy requests are supported through `target_tenancy_ocids`.
- Optional `JIT_USER_TENANCY_MAP` restricts specific requester emails to specific tenancy OCIDs.
- Requests can carry `requested_start_at`; future approvals are marked `scheduled` and group membership is added only when the expiry scheduler reaches the requested start time.
- Requester self-extension approval is blocked.
- Maximum approved extensions per request is configurable with `JIT_MAX_EXTENSIONS`.
- Approved extensions add to the remaining expiry window, capped by the originally requested duration and maximum extension count.
- Failed expiry revocations move the request to `revoke_failed`; the expiry scheduler retries until all memberships are removed and sends an admin notification/audit event to configured admins.

## Deployment Procedure

Use [DEPLOYMENT_RUNBOOK.md](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/DEPLOYMENT_RUNBOOK.md) for the step-by-step customer deployment plan.

Function deployment helpers:

- [deployment/prepare_function_contexts.sh](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/prepare_function_contexts.sh) copies shared app code, dependencies, and optional PostgreSQL CA certificate into each function build context.
- [deployment/create_ocir_function_repos.sh](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/create_ocir_function_repos.sh) pre-creates the OCIR repositories that Fn pushes each function image into.
- [deployment/deploy_functions_with_fn.sh](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/deploy_functions_with_fn.sh) runs `fn -v deploy` for all JIT functions.
- [deployment/function-app-config.example.json](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/function-app-config.example.json) is the OCI Functions application config template.
- [deployment/function-app-config.api-key.example.json](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/function-app-config.api-key.example.json) is for API-key testing.
- [deployment/function-app-config.resource-principal.example.json](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/function-app-config.resource-principal.example.json) is for resource-principal deployment.

Frontend deployment note:

- Upload every file in [frontend](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/frontend): `index.html`, `config.js`, `app.js`, and `styles.css`.
- Set `window.JIT_API_BASE` in [frontend/config.js](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/frontend/config.js) to the API Gateway prefix that serves the function routes.
- If API Gateway routes are `/jit/catalog`, `/jit/requests`, etc., set `window.JIT_API_BASE = "/jit"`.
- If API Gateway routes are `/api/catalog`, `/api/requests`, etc., keep `window.JIT_API_BASE = "/api"`.

## Production TODO

- Complete customer-specific OCI Identity Domain user lifecycle implementation if the tenancy uses identity domains rather than classic IAM users.
- Add signed Jira webhook verification.
- Store production state in OCI PostgreSQL using the dedicated JIT schema.
- Add Object Storage immutable audit export.
- Add API Gateway authorizer or IAM-signed/OIDC authenticated routes that inject verified user claims into the Function request context.
- Add Terraform variables for all function OCIDs after deployment.
- Add dead-letter/retry handling for OCI API failures.
