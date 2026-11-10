# OCI NoSQL preparation

Status: **experimental provisioning only**. OCI NoSQL is not yet implemented as an application backend. Do not use these tables for real access requests or set `JIT_DB_BACKEND=oci_nosql`. The initial table design may change when the adapter and transactional recovery are implemented. Provision only in a customer-approved test compartment and expect migration or replacement before production.

`app/nosql_store.py` contains experimental OCI SDK row-access primitives, but is not connected to the Function handlers. Its authentication and row operations have not been validated against a live OCI NoSQL service. The prospective settings `JIT_NOSQL_REGION`, `JIT_NOSQL_COMPARTMENT_OCID`, `JIT_NOSQL_TABLE_PREFIX` and existing `JIT_OCI_AUTH_MODE` are not sufficient to enable the JIT workflow. Do not deploy this as an operational NoSQL-backed application yet.

## Identity and isolation

OCI NoSQL Database Cloud Service uses OCI IAM principals, compartments, policies and tables. It does not use the PostgreSQL schema and database-user provisioning in the existing runbook. Select a customer-owned compartment and a naming convention for JIT tables. Protect them with least-privilege OCI IAM policies. For Functions, use a resource principal and a dynamic group; for controlled local tests, an API-key profile can be used. The customer IAM administrator must configure and review those policies; the application does not create IAM identities or policies.

Review [Oracle's NoSQL access guidance](https://docs.oracle.com/en/cloud/paas/nosql-cloud/kicvs/index.html) and [policy reference](https://docs.oracle.com/en/cloud/paas/nosql-cloud/odsql/) when preparing table-specific policies. Confirm private connectivity, DNS, region availability and egress controls with the customer's network team before deployment. The preflight does not establish that the endpoint is private.

## Read-only preflight

From a host with the OCI CLI configured, run:

```bash
bash deployment/check_oci_nosql.sh <compartment-ocid> <region> api_key <oci-profile>
```

For a resource principal, run the following only from an OCI resource with the appropriate resource-principal environment and IAM policy:

```bash
bash deployment/check_oci_nosql.sh <compartment-ocid> <region> resource_principal
```

The command lists NoSQL tables. An empty list can be successful. A successful result only verifies the caller can list tables; it does not prove row read/write privileges or application readiness. See the [OCI CLI reference](https://docs.oracle.com/en-us/iaas/tools/oci-cli/latest/oci_cli_docs/cmdref/nosql/table/list.html).

## Experimental table provisioning

`deployment/provision_oci_nosql.py` uses Python 3 and the OCI CLI. It starts in dry-run mode and plans five separately named tables, with a fixed set of indexes: `<prefix>_tenancies`, `_groups`, `_users`, `_requests` and `_audit`. These are initial structures, not a deployable JIT backend. Timestamp index columns use UTC ISO-8601 strings; the future adapter must enforce a consistent format. The JSON document format and request IDs are subject to change.

Before using it, confirm the target region and compartment, approved naming prefix, network path, service limits and budget. Each table reserves the requested read units, write units and storage, and may incur charges. The identity running `--apply` needs permission to list and create tables and indexes in the compartment. OCI NoSQL is an IAM-managed service, so this script does not create database users, dynamic groups, policies, or Function configuration.

Review the plan without making OCI API calls:

```bash
python3 deployment/provision_oci_nosql.py \
  --compartment-id <compartment-ocid> --region <region> \
  --auth api_key --profile <oci-profile> --prefix jit_test \
  --read-units 10 --write-units 10 --storage-gb 1
```

To create the experimental tables, repeat the command with `--apply` only after the customer approves the capacity and target compartment. The profile must be configured for the correct tenancy. Alternatively use `--auth instance_principal` on an authorized Compute instance or `--auth resource_principal` in a suitable OCI resource. The script first lists tables and stops without creating any if a planned table name exists. It never modifies or deletes existing tables. It waits for each creation work request. If any operation fails or times out, earlier resources may remain: inspect the OCI Console and work requests before taking corrective action. Rerunning with the same prefix stops on those existing tables; it does not resume or validate their schema. Never delete resources automatically to retry.

An OCI administrator must separately configure a dynamic group matching the JIT Functions and policies for the actual table names and compartment. An illustrative runtime row policy for a single table is:

```text
Allow dynamic-group <jit-functions-dynamic-group> to use nosql-rows in compartment <jit-compartment-name> where target.nosql-table.name = 'jit_test_requests'
```

Review all required table metadata, query and row permissions for the eventual adapter and constrain each table individually. Do not apply an unreviewed policy to a shared compartment. See Oracle's [NoSQL access policies](https://docs.oracle.com/en/cloud/paas/nosql-cloud/kicvs/) and [policy reference](https://docs.oracle.com/en/cloud/paas/nosql-cloud/odsql/).

## Work needed for backend support

- Define the request, membership, audit, identity and catalog data model, shard keys, indexes, capacity and retention.
- Replace direct SQL calls with a NoSQL storage implementation without silently changing the configured backend.
- Implement conditional state transitions, durable membership tracking, idempotent provisioning and revocation, and recovery after partial failures. OCI NoSQL atomic multi-row writes have shard-key constraints; external OCI IAM changes cannot be rolled back by a database transaction.
- Finalize and version table and index setup scripts, table-scoped IAM policies, migration and operational procedures alongside the final backend data model.
- Test concurrency, retries, approval separation of duties, requester visibility, scheduled activation, expiry and recovery against an isolated OCI NoSQL environment. Keep NoSQL disabled until those tests pass.
