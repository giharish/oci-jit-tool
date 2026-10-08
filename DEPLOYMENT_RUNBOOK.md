# Customer Deployment Procedure

This runbook deploys the OCI IAM JIT Access application as a production-style serverless workload:

- Static frontend portal.
- API Gateway routes.
- OCI Functions for each action.
- Durable state store.
- Jira approval integration.
- Expiry warning notification and extension workflow.

## 1. Confirm Scope

Capture these customer values before deployment:

- Parent tenancy OCID.
- Control-plane compartment OCID.
- VCN and subnet OCID for OCI Functions and API Gateway.
- Region.
- Jira base URL, project key, issue type, webhook secret, and service account.
- Existing OCI PostgreSQL database endpoint, database name, port, SSL requirement, and approved JIT schema name.
- Audit target: Object Storage bucket and/or Logging Analytics.
- Notification channel: OCI Email Delivery or OCI Notifications email topic.
- List of configured admin users or admin group claims from customer SSO. These are the only identities allowed to approve, reject, revoke, view all requests, sync catalog, or run worker actions from the portal.

## 2. Prepare OCI Prerequisites

1. Create a control-plane compartment, for example `jit-access-platform`.
2. Create or select a VCN and subnet routable by OCI Functions and API Gateway.
3. Create an OCI Functions application in the control-plane compartment.
4. Create an API Gateway in the same VCN/subnet.
5. Create a dynamic group for the Functions application.
6. Create policies that allow the Functions dynamic group to:
   - Read organization tenancies from the parent tenancy.
   - Inspect/read Terraform-managed JIT groups in target tenancies.
   - Create or read requester users where required by the customer's IAM model.
   - Add/remove users only from Terraform-managed JIT groups.
   - Read/write the chosen state store.
   - Write audit records to Object Storage or Logging.
   - Send email through the chosen notification service.

Terraform must own predefined JIT groups and policies. The application must not create or delete IAM policies or JIT groups in production. Do not grant broad `manage all-resources in tenancy` permissions to the automation principal.

## 2A. Configure OCI Discovery And Auth

OCI configuration is controlled by Function application configuration, not by a local `~/.oci/config` file when running in OCI Functions.

Production recommended values:

```json
{
  "JIT_CONNECTOR_MODE": "oci",
  "JIT_OCI_AUTH_MODE": "resource_principal",
  "JIT_ORGANIZATION_OCID": "ocid1.organization.oc1..<organization>",
  "JIT_PARENT_TENANCY_OCID": "ocid1.tenancy.oc1..<parent>",
  "JIT_GROUP_PREFIX": "JIT-",
  "JIT_ALLOW_CUSTOM_POLICIES": "false",
  "JIT_DEFAULT_DURATION_MINUTES": "60",
  "JIT_MAX_DURATION_MINUTES": "240",
  "JIT_MAX_EXTENSIONS": "2"
}
```

Resource principal mode:

- Used by OCI Functions in production.
- Requires a dynamic group that matches the Function application or Functions.
- Requires IAM policies in the parent and target tenancies to allow organization discovery, group discovery, user creation/read, and user/group membership changes.
- Uses `oci.auth.signers.get_resource_principals_signer()` inside the app.

API-key mode:

- Useful for jump-host testing and controlled break-glass diagnostics.
- Set:

```json
{
  "JIT_CONNECTOR_MODE": "oci",
  "JIT_OCI_AUTH_MODE": "api_key",
  "JIT_OCI_CONFIG_FILE": "/function/.oci/config",
  "JIT_OCI_PROFILE": "DEFAULT"
}
```

- Package the config/key into the image only for non-production testing, or mount/inject them through the customer-approved secret process.
- Do not use API keys as the preferred production auth mode for OCI Functions.

For API-key testing from the Function image, prepare a local folder that contains an OCI SDK config and private key, for example:

```text
/secure/jit-oci-api-key/
  config
  jit_api_key.pem
```

The config file should reference the packaged key path:

```ini
[DEFAULT]
user=ocid1.user.oc1..<user>
fingerprint=<fingerprint>
tenancy=ocid1.tenancy.oc1..<parent>
region=<region-identifier>
key_file=/function/.oci/jit_api_key.pem
```

Use the API-key Function config template:

```bash
cp deployment/function-app-config.api-key.example.json deployment/function-app-config.customer.json
```

Edit `deployment/function-app-config.customer.json`, then apply:

```bash
oci fn application update \
  --profile "$OCI_PROFILE" \
  --application-id "$FUNCTIONS_APP_OCID" \
  --config file://deployment/function-app-config.customer.json \
  --force
```

Package the API-key config only for testing:

```bash
bash deployment/prepare_function_contexts.sh /path/to/dbsystem.pub /secure/jit-oci-api-key
```

Or build and deploy all functions in one step:

```bash
bash deployment/deploy_functions_with_fn.sh "$FUNCTIONS_APP_NAME" /path/to/dbsystem.pub /secure/jit-oci-api-key
```

For customer production, use the resource principal template instead:

```bash
cp deployment/function-app-config.resource-principal.example.json deployment/function-app-config.customer.json
```

Tenancy discovery behavior:

- For single-tenancy testing, leave `JIT_ORGANIZATION_OCID` empty. With API-key auth, the app uses the tenancy from the selected OCI profile unless `JIT_PARENT_TENANCY_OCID` is set. With resource-principal auth, set `JIT_PARENT_TENANCY_OCID` to the target tenancy OCID.
- If `JIT_ORGANIZATION_OCID` is set later, the app uses OCI Tenant Manager Control Plane `OrganizationClient.list_organization_tenancies`.
- For each discovered tenancy, the app uses OCI Identity to list groups and keeps only groups whose name starts with `JIT_GROUP_PREFIX`.
- Request provisioning can target one or many tenancies. The selected predefined group name must exist in each requested tenancy.
- The app records all provisioned memberships and revokes each one at expiry.
- The app expects the requester to already exist in OCI IAM. It only adds/removes the existing user to/from Terraform-managed JIT groups.
- Use `JIT_USER_TENANCY_MAP` when specific requester emails must be restricted to specific child tenancies. Example: `{"user1@customer.com":["ocid1.tenancy.oc1..child1","ocid1.tenancy.oc1..child2"]}`.
- Future-dated requests are approved into `scheduled` status. The expiry scheduler activates them at `requested_start_at`, then later revokes them at expiry.

OCI Audit correlation:

- Every OCI SDK call that returns an `opc-request-id` is stored in the app audit payload where available.
- Customer operations should correlate app `audit_event` rows with OCI Audit events by timestamp, actor/requester, target tenancy OCID, group OCID, user OCID, and `opc-request-id`.
- Historical requests, approvals, expiry warnings, revocations, and revocation failures are visible to configured admins through the web interface.

## 3. Configure OCI PostgreSQL State Store

Use the customer's existing OCI PostgreSQL database. Do not create a separate database for this application unless the customer explicitly requires physical isolation. Create a separate schema in the existing database, for example `customer_jit_access`, so JIT tables, grants, and lifecycle can be managed independently.

### 3.1 Create or confirm database users

Recommended role model:

- A DBA/admin user runs the one-time schema creation script.
- A runtime application user, for example `jit_access_app`, is used by OCI Functions.
- The runtime user receives DML permissions only. Do not give the Function runtime user superuser, database-owner, or broad DDL privileges.

If the runtime user does not already exist, create it through the customer-approved DBA process. Example:

```sql
create role jit_access_app login password '<use-vault-managed-secret>';
```

For production, store the password in OCI Vault and inject it into Functions as a secret or configuration value. Do not store it in source control.

### 3.2 Create the JIT schema in the existing database

From a bastion, Cloud Shell, or administrator workstation that can reach the OCI PostgreSQL private endpoint:

```bash
cd /Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app

psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=customer_jit_access \
  -v app_role=jit_access_app \
  -f database/postgresql_schema.sql
```

Use a different customer-approved schema name if `customer_jit_access` is not acceptable:

```bash
psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=<customer_schema_name> \
  -v app_role=jit_access_app \
  -f database/postgresql_schema.sql
```

The script creates these tables inside the selected schema:

- `tenancy_catalog`
- `group_catalog`
- `access_request`
- `audit_event`

It also creates indexes for requester lookup, expiry workers, and audit review.

### 3.3 Validate schema isolation

First validate through PostgreSQL catalog tables. This confirms whether the schema exists even if the current user does not yet have `information_schema` visibility:

```bash
psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=customer_jit_access \
  -v app_role=jit_access_app \
  -f database/postgresql_verify.sql
```

Or run the table check manually:

```sql
select
  n.nspname as table_schema,
  c.relname as object_name,
  c.relkind as object_type
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'customer_jit_access'
  and c.relkind in ('r', 'i', 'S')
order by c.relkind, c.relname;
```

Expected tables:

- `access_request`
- `audit_event`
- `group_catalog`
- `tenancy_catalog`

Confirm runtime grants:

```sql
select table_schema, table_name, privilege_type
from information_schema.role_table_grants
where grantee = 'jit_access_app'
  and table_schema = 'customer_jit_access'
order by table_name, privilege_type;
```

If both the table query and grants query return `0 rows`, the schema script did not apply to the database you are currently connected to. Run these checks:

```sql
select current_database(), current_user, inet_server_addr(), inet_server_port();

select rolname, rolcanlogin
from pg_roles
where rolname = 'jit_access_app';

select nspname
from pg_namespace
where nspname = 'customer_jit_access';
```

Common fixes:

- If `jit_access_app` is missing, create the runtime role first, then rerun `database/postgresql_schema.sql`.
- If `current_database()` is not the intended database, reconnect to the correct existing customer database and rerun the schema script.
- If the schema exists in `pg_namespace` but `information_schema.tables` returns no rows, reconnect as the DBA/schema owner and rerun the grants section, or run the full schema script again with the correct `app_role`.
- If the script printed an error such as `role "jit_access_app" does not exist`, PostgreSQL rolled back the transaction and no schema/tables were created.

To create the runtime role when the DBA approves it:

```sql
create role jit_access_app login password '<use-vault-managed-secret>';
```

Then rerun:

```bash
psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=customer_jit_access \
  -v app_role=jit_access_app \
  -f database/postgresql_schema.sql
```

If the schema already exists from an earlier deployment, apply the multi-tenancy migration:

```bash
psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=customer_jit_access \
  -f database/postgresql_migration_002_multi_tenancy.sql
```

Then apply the self-service portal user migration:

```bash
psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=customer_jit_access \
  -v app_role=jit_access_app \
  -f database/postgresql_migration_004_app_users.sql
```

Then add email verification fields:

```bash
psql "host=<postgres-private-endpoint> port=5432 dbname=<existing_database> user=<admin_user> sslmode=require" \
  -v jit_schema=customer_jit_access \
  -f database/postgresql_migration_005_email_verification.sql
```

### 3.4 Configure `verify-full` SSL certificate handling

OCI Database with PostgreSQL supports `sslmode=verify-full`, which requires a trusted CA certificate file and verifies that the host name in the connection string matches the server certificate. Use the database endpoint FQDN, not an IP address, unless the certificate includes the IP address in its SAN.

Download the CA certificate from the OCI PostgreSQL database system connection details. The examples below assume the file is named `dbsystem.pub`.

Recommended options:

- **Function image path**: copy the public CA certificate into the Function image, for example `/function/certs/dbsystem.pub`, and set `JIT_POSTGRES_SSLROOTCERT_PATH=/function/certs/dbsystem.pub`.
- **Vault/injected PEM**: store the PEM content in OCI Vault and inject it into `JIT_POSTGRES_SSLROOTCERT_PEM`. The app writes the PEM to `/tmp/jit_postgres_root.crt` at startup and appends that path to the PostgreSQL connection string.

The CA certificate is not a password, but keeping it in Vault can simplify rotation and customer-controlled release processes. The database password should always come from Vault or an equivalent secret manager.

Do not pass the certificate body directly as `sslrootcert`; PostgreSQL/libpq expects `sslrootcert` to be a file path.

### 3.5 Configure Functions to use OCI PostgreSQL

Set these Function application configuration values:

```bash
fn config app <functions-app-name> JIT_DB_BACKEND postgresql
fn config app <functions-app-name> JIT_POSTGRES_SCHEMA customer_jit_access
fn config app <functions-app-name> JIT_AUTO_INIT_DB false
fn config app <functions-app-name> JIT_POSTGRES_DSN "host=<postgres-fqdn> port=5432 dbname=<existing_database> user=jit_access_app password=<vault-or-injected-secret> sslmode=verify-full"
fn config app <functions-app-name> JIT_POSTGRES_SSLROOTCERT_PATH "/function/certs/dbsystem.pub"
```

The stored `JIT_POSTGRES_DSN` value must start directly with `host=`. Do not paste smart quotes or include quote characters inside the DSN value. In JSON, the outer JSON quotes are required, but the value itself should be:

```json
"JIT_POSTGRES_DSN": "host=<postgres-fqdn> port=5432 dbname=<existing_database> user=jit_access_app password=<secret> sslmode=verify-full"
```

It must not be:

```json
"JIT_POSTGRES_DSN": "“host=<postgres-fqdn> port=5432 dbname=<existing_database> user=jit_access_app password=<secret> sslmode=verify-full”"
```

If the jump host does not have a working Fn Project CLI context, use OCI CLI instead. Create a JSON file from [deployment/function-app-config.example.json](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/function-app-config.example.json), update the values, then run:

```bash
oci fn application update \
  --application-id <function-application-ocid> \
  --config file://deployment/function-app-config.example.json \
  --force
```

To confirm the values:

```bash
oci fn application get \
  --application-id <function-application-ocid> \
  --query 'data.config'
```

Important: OCI Functions application configuration is delivered to Functions as environment variables. OCI documents a 4 KB maximum for the serialized configuration. Because of that limit, the recommended production option is `JIT_POSTGRES_SSLROOTCERT_PATH` with the CA certificate packaged in the Function image. Use `JIT_POSTGRES_SSLROOTCERT_PEM` only if the full PEM plus all other config remains comfortably below the 4 KB limit.

You can also configure the same values in the OCI Console:

1. Open **Developer Services** > **Functions** > **Applications**.
2. Select the JIT Function application.
3. Open **Configuration**.
4. Add or update the same keys shown above.
5. Save changes and invoke a Function after the change is applied.

Preferred production pattern:

- Keep the password in OCI Vault.
- Inject the resolved secret into the Function configuration during deployment.
- Ensure the Function subnet can reach the OCI PostgreSQL private endpoint on port `5432`.
- Keep the schema DDL in [database/postgresql_schema.sql](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/database/postgresql_schema.sql) under change control.

If you prefer Vault-injected certificate material instead of an image path:

```bash
fn config app <functions-app-name> JIT_POSTGRES_SSLROOTCERT_PEM "$(cat dbsystem.pub)"
```

For multiline PEM values, use the customer's deployment pipeline or OCI secret retrieval step to preserve newlines correctly.

### 3.6 Connection and availability checks

Before deploying the Functions, confirm:

- DNS resolves the OCI PostgreSQL endpoint from the Function subnet.
- Security lists or NSGs allow egress from Functions to PostgreSQL on `5432`.
- PostgreSQL ingress allows the Function subnet or NSG.
- SSL mode is aligned with the customer's OCI PostgreSQL configuration.
- The `jit_access_app` user can select, insert, update, and delete only inside the JIT schema.

## 4. Configure Notification Delivery

The application sends account verification emails, expiry warnings when an active grant has less than 15 minutes remaining, and admin notifications when revocation fails.

Recommended options:

- OCI Email Delivery for direct application-generated email.
- Customer SMTP relay if notification delivery is already centralized.
- OCI Notifications if customer wants topic/subscription management. This requires adding a publisher adapter with the same function contract.

For local demos, keep:

```bash
JIT_NOTIFICATION_MODE=mock
```

Mock mode returns notification metadata but does not send email.

For real email through OCI Email Delivery or a customer SMTP relay:

```bash
JIT_NOTIFICATION_MODE=smtp
JIT_SMTP_HOST=smtp.email.<region>.oci.oraclecloud.com
JIT_SMTP_PORT=587
JIT_SMTP_USERNAME=<smtp-user>
JIT_SMTP_PASSWORD=<smtp-password>
JIT_SMTP_SENDER=jit-access@customer.com
JIT_SMTP_STARTTLS=true
```

For OCI Email Delivery, create an approved sender, generate SMTP credentials, and ensure the Function subnet can reach the regional SMTP endpoint on port `587`. For customer notification infrastructure, use their SMTP host, port, credentials, and approved sender address.

Store SMTP username/password in OCI Vault or the customer's secret platform and inject them into the Functions app configuration during deployment.

The warning worker is idempotent per active timer window. After an approved extension, `warning_sent_at` is reset so a new warning can be sent for the new expiry.

## 5. Configure Extension Policy

Extension rules implemented:

- A requester can ask for an extension at any time while access is active.
- A single approved extension can be for no more than the originally requested duration.
- Approved extensions add to the remaining expiry window. If the previous expiry has already passed, the extension is added from the approval time.
- Maximum approved extensions per request: `2`.
- Requesters cannot approve their own extension.
- Configured admins can approve or reject extension requests.
- Anonymous extension requests require the secure token from the warning email. Authenticated users can request extensions only for their own access grants.

For production, expose extension approvals through Jira or the in-portal approval queue, depending on customer governance.

## 6. Create, Build, And Deploy Functions

OCI Functions runs Docker images. The Fn Project CLI can build each function image, push it to OCIR, and create or update the OCI Function with one command: `fn -v deploy --app <functions-app-name>`.

This application has multiple small function entrypoints under `functions/`, but they share common backend code from `app/`. Before building images, prepare each function directory so it contains:

- Its own `func.py`.
- Its own `func.yaml`.
- A copied `app/` package.
- A copied `requirements.txt`.
- Optional PostgreSQL CA certificate at `certs/dbsystem.pub`.

The function manifests use `runtime: python` with explicit Python 3.11 FDK images:

```yaml
runtime: python
build_image: fnproject/python:3.11-dev
run_image: fnproject/python:3.11
```

This avoids failures on jump hosts where the Fn CLI rejects `runtime: python3.11` even though the Python 3.11 FDK images are supported. If the jump host still rejects the build, upgrade the Fn CLI to a current release or temporarily switch `build_image` and `run_image` to `fnproject/python:3.9-dev` and `fnproject/python:3.9`; the current application code is compatible with Python 3.9+.

### 6.1 Set Deployment Variables

Set these values on the jump host:

```bash
export OCI_PROFILE=<oci-profile-name>
export REGION_IDENTIFIER=<region-identifier>
export REGION_KEY=<region-key>
export FUNCTIONS_COMPARTMENT_OCID=<functions-compartment-ocid>
export FUNCTIONS_SUBNET_OCID=<functions-subnet-ocid>
export FUNCTIONS_APP_NAME=jit-access-functions
export OCIR_NAMESPACE=<tenancy-namespace>
export OCIR_REPO_PREFIX=jit-access
```

Examples:

```bash
export REGION_IDENTIFIER=ap-mumbai-1
export REGION_KEY=bom
```

Get the OCIR namespace if needed:

```bash
oci os ns get --profile "$OCI_PROFILE" --query data --raw-output
```

### 6.2 Create The OCI Functions Application

Create a subnet JSON file:

```bash
cd /Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app
printf '["%s"]\n' "$FUNCTIONS_SUBNET_OCID" > deployment/subnet-ids.json
```

Create the application:

```bash
FUNCTIONS_APP_OCID=$(
  oci fn application create \
    --profile "$OCI_PROFILE" \
    --compartment-id "$FUNCTIONS_COMPARTMENT_OCID" \
    --display-name "$FUNCTIONS_APP_NAME" \
    --subnet-ids file://deployment/subnet-ids.json \
    --query 'data.id' \
    --raw-output
)

echo "$FUNCTIONS_APP_OCID"
```

If the application already exists, look it up instead:

```bash
FUNCTIONS_APP_OCID=$(
  oci fn application list \
    --profile "$OCI_PROFILE" \
    --compartment-id "$FUNCTIONS_COMPARTMENT_OCID" \
    --display-name "$FUNCTIONS_APP_NAME" \
    --query 'data[0].id' \
    --raw-output
)
```

### 6.3 Configure Function Application Environment

Update [deployment/function-app-config.example.json](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/deployment/function-app-config.example.json) with customer values, then apply it:

```bash
oci fn application update \
  --profile "$OCI_PROFILE" \
  --application-id "$FUNCTIONS_APP_OCID" \
  --config file://deployment/function-app-config.example.json \
  --force
```

Confirm:

```bash
oci fn application get \
  --profile "$OCI_PROFILE" \
  --application-id "$FUNCTIONS_APP_OCID" \
  --query 'data.config'
```

### 6.4 Configure Fn CLI And OCIR Login

Create or select the Fn context:

```bash
fn create context jit-access --provider oracle
fn use context jit-access
fn update context oracle.profile "$OCI_PROFILE"
fn update context oracle.compartment-id "$FUNCTIONS_COMPARTMENT_OCID"
fn update context api-url "https://functions.${REGION_IDENTIFIER}.oci.oraclecloud.com"
fn update context registry "${REGION_KEY}.ocir.io/${OCIR_NAMESPACE}/${OCIR_REPO_PREFIX}"
```

Important: `OCIR_REPO_PREFIX` is a prefix, not the single final repository used by every function. If you set:

```bash
export OCIR_REPO_PREFIX=jit-repo
fn update context registry "${REGION_KEY}.ocir.io/${OCIR_NAMESPACE}/${OCIR_REPO_PREFIX}"
```

then Fn deploys each function image to a separate OCIR repository name under that prefix:

```text
jit-repo/auth-session-fn
jit-repo/catalog-sync-fn
jit-repo/request-access-fn
jit-repo/approval-callback-fn
...
```

OCI Container Registry supports slashes in repository names, but the slash is not a folder hierarchy. `jit-repo` and `jit-repo/catalog-sync-fn` are different repositories. If the deploying user cannot create repositories automatically, pre-create them:

```bash
bash deployment/create_ocir_function_repos.sh "$FUNCTIONS_COMPARTMENT_OCID" "$OCIR_REPO_PREFIX" "$OCI_PROFILE"
```

Alternative: if you only want top-level function repositories, set the registry without a repo prefix:

```bash
fn update context registry "${REGION_KEY}.ocir.io/${OCIR_NAMESPACE}"
```

Then Fn pushes images to:

```text
auth-session-fn
catalog-sync-fn
request-access-fn
approval-callback-fn
...
```

Log in to OCIR. Use an auth token, not the OCI Console password:

```bash
docker login "${REGION_KEY}.ocir.io"
```

Username format is usually:

```text
<tenancy-namespace>/<username>
```

For federated users, use the customer-approved OCIR username format for their identity provider.

Verify the Fn context:

```bash
fn inspect context
fn list apps
```

### 6.5 Prepare Function Build Contexts

If `sslmode=verify-full` uses a certificate inside the image, download the PostgreSQL CA certificate as `dbsystem.pub` from the OCI PostgreSQL database system connection details, then run:

```bash
bash deployment/prepare_function_contexts.sh /path/to/dbsystem.pub
```

If using `JIT_POSTGRES_SSLROOTCERT_PEM` instead, no image certificate is required:

```bash
bash deployment/prepare_function_contexts.sh
```

The helper script copies shared code and requirements into each function folder. It also copies the certificate to:

```text
functions/<function-name>/certs/dbsystem.pub
```

At runtime this is available inside the Function image as:

```text
/function/certs/dbsystem.pub
```

That is why `JIT_POSTGRES_SSLROOTCERT_PATH` should be:

```text
/function/certs/dbsystem.pub
```

### 6.6 Build Images And Deploy All Functions

If the Function application already exists and configuration parameters are already set, this is the only deployment path you need.

Deploy all functions:

```bash
bash deployment/deploy_functions_with_fn.sh "$FUNCTIONS_APP_NAME" /path/to/dbsystem.pub
```

Or deploy manually one function at a time:

```bash
cd /Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/functions/catalog_sync
fn -v deploy --app "$FUNCTIONS_APP_NAME"
```

Repeat manually for:

- `catalog_sync`
- `request_access`
- `approval_callback`
- `provision_access`
- `policy_builder`
- `revoke_access`
- `request_extension`
- `approve_extension`
- `notification_worker`
- `expiry_scheduler`

Each `fn -v deploy` run builds a Docker image from that function directory, pushes it to the configured OCIR repository, and creates or updates the corresponding OCI Function in the application.

Existing-app fast path:

```bash
cd /Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app

export FUNCTIONS_APP_NAME=<existing-functions-app-name>

# Use this when sslmode=verify-full uses a cert packaged in the image.
bash deployment/prepare_function_contexts.sh /path/to/dbsystem.pub

for function_dir in \
  catalog_sync \
  request_access \
  approval_callback \
  provision_access \
  policy_builder \
  revoke_access \
  request_extension \
  approve_extension \
  notification_worker \
  expiry_scheduler
do
  (
    cd "functions/${function_dir}"
    fn -v deploy --app "$FUNCTIONS_APP_NAME"
  )
done
```

If the Function display name from `func.yaml` does not already exist in the app, `fn deploy` creates it. If it already exists, `fn deploy` updates it with the newly built image.

### 6.7 Verify Function Images And OCIDs

List deployed functions:

```bash
oci fn function list \
  --profile "$OCI_PROFILE" \
  --application-id "$FUNCTIONS_APP_OCID" \
  --all \
  --query 'data[].{name:"display-name",id:id,image:image,memory:"memory-in-mbs"}' \
  --output table
```

Record the OCIDs for API Gateway:

| Function directory | Function display name |
|---|---|
| `auth_session` | `auth-session-fn` |
| `catalog_sync` | `catalog-sync-fn` |
| `request_access` | `request-access-fn` |
| `approval_callback` | `approval-callback-fn` |
| `provision_access` | `provision-access-fn` |
| `policy_builder` | `policy-builder-fn` |
| `revoke_access` | `revoke-access-fn` |
| `request_extension` | `request-extension-fn` |
| `approve_extension` | `approve-extension-fn` |
| `notification_worker` | `notification-worker-fn` |
| `expiry_scheduler` | `expiry-scheduler-fn` |

### 6.8 Invoke A Function Smoke Test

Invoke catalog sync:

```bash
echo '{"action":"sync"}' > /tmp/catalog-sync.json

fn invoke "$FUNCTIONS_APP_NAME" catalog-sync-fn < /tmp/catalog-sync.json
```

If this fails, check:

- Fn context points to the right compartment and region.
- OCIR login is valid.
- The Function subnet can reach OCI PostgreSQL on `5432`.
- `JIT_POSTGRES_SCHEMA=customer_jit_access`.
- `JIT_POSTGRES_SSLROOTCERT_PATH=/function/certs/dbsystem.pub` when using image-packaged certificate.
- The Function dynamic group has required OCI IAM permissions.

## 7. Deploy API Gateway Routes

Update `terraform/terraform.tfvars` or pass variables with:

```hcl
compartment_ocid = "ocid1.compartment.oc1.."
subnet_ocid      = "ocid1.subnet.oc1.."
function_ocids = {
  auth               = "ocid1.fnfunc.oc1.."
  catalog            = "ocid1.fnfunc.oc1.."
  request            = "ocid1.fnfunc.oc1.."
  approval           = "ocid1.fnfunc.oc1.."
  policy             = "ocid1.fnfunc.oc1.."
  revoke             = "ocid1.fnfunc.oc1.."
  extension_request  = "ocid1.fnfunc.oc1.."
  extension_approval = "ocid1.fnfunc.oc1.."
  notification       = "ocid1.fnfunc.oc1.."
  expiry             = "ocid1.fnfunc.oc1.."
}
```

Then run:

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Confirm the API endpoint from Terraform output.

## 8. Deploy Frontend Portal

Recommended options:

1. Object Storage static website + API Gateway URL configured in frontend.
2. OCI Load Balancer + static web server.
3. Customer internal web platform.

For production, add SSO/OIDC or IAM authentication in front of API Gateway. The portal must never offer a user selector or accept a browser-supplied actor as authority.

Authentication options:

- Production/customer IdP: set `JIT_PORTAL_AUTH_MODE=trusted_header`. Configure API Gateway/OIDC/IAM middleware to authenticate the user, strip inbound client identity headers, then inject a trusted user claim such as `X-Authenticated-User`.
- Built-in portal login: set `JIT_PORTAL_AUTH_MODE=builtin`, configure `JIT_SESSION_SECRET`, and keep `JIT_ALLOW_SELF_REGISTRATION=true` if requesters should create their own accounts. Self-service accounts are stored in the `app_user` table with hashed passwords.
- Mixed mode: set `JIT_PORTAL_AUTH_MODE=mixed` when the customer wants both built-in users and IdP/OIDC users.
- Admin rights still require the same email to be present in `JIT_AUTHORIZED_USERS`.

Generate a built-in password hash:

```bash
cd /Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app
python3 deployment/hash_builtin_password.py
```

Example built-in auth Function config:

```bash
fn config app "$FUNCTIONS_APP_NAME" JIT_PORTAL_AUTH_MODE builtin
fn config app "$FUNCTIONS_APP_NAME" JIT_ALLOW_SELF_REGISTRATION true
fn config app "$FUNCTIONS_APP_NAME" JIT_ALLOW_ANONYMOUS_REQUESTS false
fn config app "$FUNCTIONS_APP_NAME" JIT_ALLOWED_EMAIL_DOMAINS customer.com,oracle.com
fn config app "$FUNCTIONS_APP_NAME" JIT_REQUIRE_EMAIL_VERIFICATION true
fn config app "$FUNCTIONS_APP_NAME" JIT_PORTAL_BASE_URL "https://<gateway-host>/jit/"
fn config app "$FUNCTIONS_APP_NAME" JIT_SESSION_SECRET "$(openssl rand -base64 48)"
fn config app "$FUNCTIONS_APP_NAME" JIT_BUILTIN_USERS '{"admin.email@customer.com":{"password_hash":"pbkdf2_sha256$260000$..."}}'
fn config app "$FUNCTIONS_APP_NAME" JIT_AUTHORIZED_USERS '{"admin.email@customer.com":"admin"}'
```

Required frontend changes before go-live:

- Upload `index.html`, `config.js`, `app.js`, and `styles.css` from [frontend](/Users/girishraja/Documents/ChatGPT/Optimization/jit-production-app/frontend).
- Set `window.JIT_API_BASE` in `config.js` to the API Gateway function route prefix.
- If the API Gateway deployment path prefix is `/jit` and the function routes are `/catalog`, `/requests`, etc., set `window.JIT_API_BASE = "/jit"`.
- If the API Gateway deployment exposes `/api/catalog`, `/api/requests`, etc., set `window.JIT_API_BASE = "/api"`.
- Use authenticated user claims injected by API Gateway/OIDC/IAM middleware.
- Strip any inbound `X-Authenticated-User`, `X-Forwarded-Email`, or equivalent identity headers from public clients before the trusted gateway layer sets them.
- Map only configured admin claims to the admin role. Everyone else is a requester.
- Keep the requester email read-only for authenticated users. Anonymous request creation and request lookup should remain disabled for production.

## 9. Configure Scheduled Workers

Create schedules for:

- `catalog-sync-fn`: every 15-60 minutes.
- `notification-worker-fn`: every 1-5 minutes.
- `expiry-scheduler-fn`: every 1-5 minutes.

The notification worker sends email when remaining time is less than 15 minutes. The expiry scheduler activates scheduled requests once their requested start time arrives, and revokes active access once the timer reaches zero. If revocation fails, the request is marked `revoke_failed`, an admin notification/audit event is created for configured admins, and the expiry scheduler retries on later runs.

## 10. Configure Jira Approval

1. Create a Jira project or use an approved existing project.
2. Create approval workflow statuses.
3. Configure webhook callback to API Gateway route `/approval`.
4. Sign webhook payloads or use a shared secret.
5. Update `MockJiraGateway` with Jira REST issue creation and callback verification.

For extensions, either:

- Create a new Jira approval item for each extension request, or
- Route extension approvals to the in-portal configured-admin queue.

## 11. Run Production Smoke Test

1. Run catalog sync.
2. Confirm parent + child tenancies are visible in the portal.
3. Confirm `tenancy_catalog` and `group_catalog` rows were written in the configured PostgreSQL schema.
4. Select a child tenancy and confirm groups load dynamically.
5. Submit standard group request.
6. Confirm an `access_request` row is created in PostgreSQL.
7. Approve through Jira or portal.
8. Confirm user is attached only to the selected target-tenancy group.
9. Confirm timer appears in portal.
10. Force or wait until less than 15 minutes remain.
11. Confirm warning email is sent to requester.
12. Request an extension.
13. Approve extension.
14. Confirm timer resets to the approved extension duration.
15. Repeat extension once more.
16. Confirm a third extension request is rejected.
17. Let timer expire or run expiry worker.
18. Confirm group membership is removed and audit records are written to `audit_event`.

## 12. Operational Handover

Provide customer operations with:

- API Gateway endpoint.
- Function OCIDs and deployment versions.
- OCI PostgreSQL endpoint, database name, JIT schema name, runtime username, and secret location.
- Audit bucket/log group.
- Notification topic or Email Delivery sender.
- Jira project/webhook details.
- Break-glass procedure.
- Rollback procedure.

## References

- OCI Functions deployment: https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsuploading.htm
- API Gateway Functions backend: https://docs.oracle.com/en-us/iaas/Content/APIGateway/Tasks/apigatewayusingfunctionsbackend.htm
- OCI Notifications subscriptions: https://docs.oracle.com/en-us/iaas/Content/Notification/Tasks/create-subscription.htm
- OCI Email Delivery getting started: https://docs.oracle.com/en-us/iaas/Content/Email/Reference/gettingstarted.htm
