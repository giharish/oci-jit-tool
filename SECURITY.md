# Security Baseline

This repository is intended to be handed over as a customer-controlled OCI IAM JIT access PoC. Treat the code, Function image, database schema, and API Gateway policies as one deployment unit.

## Branch And Review Guardrails

- Do not commit directly to `main`.
- Use feature branches with the `oci-tool/` prefix.
- Merge only through pull requests after review and test evidence is attached.

## Runtime Authentication

- Production IdP mode should use `JIT_PORTAL_AUTH_MODE=trusted_header` behind API Gateway/OIDC/IAM middleware.
- Built-in login mode should use `JIT_PORTAL_AUTH_MODE=builtin`, `JIT_SESSION_SECRET`, allowed email domains, and email verification.
- In built-in mode the backend ignores trusted identity headers to reduce accidental header spoofing.
- In trusted-header mode the API Gateway must strip inbound identity headers from public clients before injecting its own authenticated user claim.

## Secrets

- Do not commit database passwords, API keys, SMTP passwords, Jira secrets, private keys, or session secrets.
- Prefer OCI Vault or the customer's approved secret manager.
- API-key OCI auth is for testing only. Use resource principals for customer deployment unless the customer explicitly approves another model.
- If PostgreSQL uses `sslmode=verify-full`, pass the CA certificate through a controlled image build or Vault-backed deployment process.

## Application Hardening

- Function errors return generic 500 responses by default. Set `JIT_DEBUG_ERRORS=true` only in isolated troubleshooting environments.
- API responses include `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
- The local simulator sends defensive browser headers, including a Content Security Policy.
- The frontend escapes dynamic API data before rendering it into HTML.
- Requesters can view only their own requests; configured admins can view and act on all requests.

## Image Vulnerability Scanning

Scan the exact OCIR image references that will be deployed:

```bash
bash deployment/security_scan.sh <region-key>.ocir.io/<namespace>/jit-repo/request_access:<tag>
```

Repeat for every Function image, or pass all image references in one command. The script fails the release if high or critical vulnerabilities are present.

## Dependency Scanning

Run dependency checks on the build host or CI runner:

```bash
python3 -m pip install pip-audit
pip-audit -r functions/requirements.txt
```

Treat high or critical dependency findings as release blockers unless the customer security team accepts a documented exception.

## Deployment Checklist

- Apply PostgreSQL migrations to the customer schema.
- Confirm runtime DB role has only required DML privileges.
- Configure API Gateway routes to require authentication for all API paths.
- Configure static frontend hosting with equivalent security headers.
- Verify `JIT_AUTHORIZED_USERS` contains only approved approvers/admins.
- Run unit tests, image scanning, and dependency scanning before customer deployment.
