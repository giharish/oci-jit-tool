import json
import os


DEFAULT_AUTHORIZED_USERS = {
    "platform.owner@customer.example": "admin",
    "iam.admin@customer.example": "admin",
}

DEFAULT_GROUP_TEMPLATES = [
    {
        "name": "JIT-Compute-Operator",
        "description": "Operate compute instances in approved compartments.",
        "policy_summary": "use instance-family; read virtual-network-family",
        "access_template": "compute_operator",
    },
    {
        "name": "JIT-Network-Viewer",
        "description": "Read-only network inspection.",
        "policy_summary": "read virtual-network-family",
        "access_template": "network_viewer",
    },
    {
        "name": "JIT-DBA-Limited",
        "description": "Limited database operational access.",
        "policy_summary": "use database-family",
        "access_template": "dba_limited",
    },
    {
        "name": "JIT-Audit-Reader",
        "description": "Read audit and resource metadata.",
        "policy_summary": "read audit-events; inspect all-resources",
        "access_template": "audit_reader",
    },
]

SERVICE_ALLOWLIST = {
    "instance-family": {"verbs": ["inspect", "read", "use", "manage"], "admin_required_for": ["manage"]},
    "virtual-network-family": {"verbs": ["inspect", "read", "use", "manage"], "admin_required_for": ["use", "manage"]},
    "volume-family": {"verbs": ["inspect", "read", "use", "manage"], "admin_required_for": ["manage"]},
    "object-family": {"verbs": ["inspect", "read", "use", "manage"], "admin_required_for": ["manage"]},
    "database-family": {"verbs": ["inspect", "read", "use", "manage"], "admin_required_for": ["use", "manage"]},
    "audit-events": {"verbs": ["read"], "admin_required_for": []},
}


def env_json(name, default):
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be valid JSON") from exc


def env_list(name, default=None):
    raw = os.environ.get(name)
    if not raw:
        return default or []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]


def app_config():
    db_backend = os.environ.get("JIT_DB_BACKEND", "sqlite").lower()
    return {
        "db_backend": db_backend,
        "db_path": os.environ.get("JIT_DB_PATH", "./data/jit_production_app.db"),
        "postgres_dsn": os.environ.get("JIT_POSTGRES_DSN"),
        "postgres_schema": os.environ.get("JIT_POSTGRES_SCHEMA", "customer_jit_access"),
        "postgres_sslrootcert_path": os.environ.get("JIT_POSTGRES_SSLROOTCERT_PATH"),
        "postgres_sslrootcert_pem": os.environ.get("JIT_POSTGRES_SSLROOTCERT_PEM"),
        "auto_init_db": os.environ.get("JIT_AUTO_INIT_DB", "true" if db_backend == "sqlite" else "false").lower() == "true",
        "mode": os.environ.get("JIT_CONNECTOR_MODE", "mock"),
        "oci_auth_mode": os.environ.get("JIT_OCI_AUTH_MODE", "resource_principal").lower(),
        "oci_config_file": os.environ.get("JIT_OCI_CONFIG_FILE", "~/.oci/config"),
        "oci_profile": os.environ.get("JIT_OCI_PROFILE", "DEFAULT"),
        "organization_ocid": os.environ.get("JIT_ORGANIZATION_OCID") or None,
        "jit_group_prefix": os.environ.get("JIT_GROUP_PREFIX", "JIT-"),
        "allow_custom_policies": os.environ.get("JIT_ALLOW_CUSTOM_POLICIES", "false").lower() == "true",
        "default_duration_minutes": int(os.environ.get("JIT_DEFAULT_DURATION_MINUTES", "60")),
        "max_duration_minutes": int(os.environ.get("JIT_MAX_DURATION_MINUTES", "240")),
        "max_extensions": int(os.environ.get("JIT_MAX_EXTENSIONS", "2")),
        "jira_mode": os.environ.get("JIT_JIRA_MODE", "mock"),
        "notification_mode": os.environ.get("JIT_NOTIFICATION_MODE", "mock").lower(),
        "smtp_host": os.environ.get("JIT_SMTP_HOST"),
        "smtp_port": int(os.environ.get("JIT_SMTP_PORT", "587")),
        "smtp_username": os.environ.get("JIT_SMTP_USERNAME"),
        "smtp_password": os.environ.get("JIT_SMTP_PASSWORD"),
        "smtp_sender": os.environ.get("JIT_SMTP_SENDER"),
        "smtp_starttls": os.environ.get("JIT_SMTP_STARTTLS", "true").lower() == "true",
        "smtp_timeout_seconds": int(os.environ.get("JIT_SMTP_TIMEOUT_SECONDS", "20")),
        "portal_title": os.environ.get("JIT_PORTAL_TITLE", "OCI IAM JIT Access"),
        "portal_auth_mode": os.environ.get("JIT_PORTAL_AUTH_MODE", "trusted_header").lower(),
        "allow_self_registration": os.environ.get("JIT_ALLOW_SELF_REGISTRATION", "true").lower() == "true",
        "allow_anonymous_requests": os.environ.get("JIT_ALLOW_ANONYMOUS_REQUESTS", "false").lower() == "true",
        "allowed_email_domains": [item.lower().lstrip("@") for item in env_list("JIT_ALLOWED_EMAIL_DOMAINS", [])],
        "user_tenancy_map": env_json("JIT_USER_TENANCY_MAP", {}),
        "require_email_verification": os.environ.get("JIT_REQUIRE_EMAIL_VERIFICATION", "true").lower() == "true",
        "portal_base_url": os.environ.get("JIT_PORTAL_BASE_URL"),
        "session_secret": os.environ.get("JIT_SESSION_SECRET"),
        "session_ttl_minutes": int(os.environ.get("JIT_SESSION_TTL_MINUTES", "480")),
        "builtin_users": env_json("JIT_BUILTIN_USERS", {}),
        "authorized_users": env_json("JIT_AUTHORIZED_USERS", DEFAULT_AUTHORIZED_USERS),
        "group_templates": env_json("JIT_GROUP_TEMPLATES", DEFAULT_GROUP_TEMPLATES),
        "service_allowlist": env_json("JIT_SERVICE_ALLOWLIST", SERVICE_ALLOWLIST),
        "mock_child_tenancy_count": int(os.environ.get("JIT_MOCK_CHILD_TENANCY_COUNT", "25")),
        "parent_tenancy_ocid": os.environ.get("JIT_PARENT_TENANCY_OCID") or None,
    }


def role_for(email):
    users = {key.lower(): value.lower() for key, value in app_config()["authorized_users"].items()}
    return users.get(str(email or "").lower(), "requester")


def is_configured_admin(email):
    return role_for(email) == "admin"
