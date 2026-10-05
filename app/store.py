import json
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import app_config


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dumps(value):
    return json.dumps({} if value is None else value, sort_keys=True)


def loads(value, default=None):
    if not value:
        return default if default is not None else {}
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def serialize_value(value):
    if isinstance(value, datetime):
        item = value
        if item.tzinfo is None:
            item = item.replace(tzinfo=timezone.utc)
        return item.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return value


class PostgresConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        return self.conn.execute(query.replace("?", "%s"), params or ())

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def validate_identifier(value, name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise RuntimeError(f"{name} must be a simple PostgreSQL identifier")


def normalize_postgres_dsn(dsn):
    value = str(dsn or "").strip()
    value = value.replace("\\\"", "\"").strip()
    for _ in range(3):
        cleaned = value.strip("\"'“”‘’").strip()
        if cleaned == value:
            break
        value = cleaned
    if any(mark in value for mark in ("“", "”", "‘", "’")):
        raise RuntimeError("JIT_POSTGRES_DSN contains smart quotes; use plain ASCII quotes only in shell/JSON and do not include quotes inside the DSN value")
    if value.startswith('"') or value.startswith("'") or value.startswith("\\"):
        raise RuntimeError("JIT_POSTGRES_DSN must begin with host=, not a quote character")
    return value


def postgres_dsn(config):
    dsn = normalize_postgres_dsn(config["postgres_dsn"])
    if "sslrootcert=" in dsn:
        return dsn
    cert_path = config.get("postgres_sslrootcert_path")
    cert_pem = config.get("postgres_sslrootcert_pem")
    if cert_pem:
        cert_path = str(Path(tempfile.gettempdir()) / "jit_postgres_root.crt")
        if "\\n" in cert_pem and "\n" not in cert_pem:
            cert_pem = cert_pem.replace("\\n", "\n")
        Path(cert_path).write_text(cert_pem, encoding="utf-8")
    if cert_path:
        return f"{dsn} sslrootcert={cert_path}"
    return dsn


@contextmanager
def connect():
    config = app_config()
    if config["db_backend"] == "postgresql":
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg import sql
        except ImportError as exc:
            raise RuntimeError("PostgreSQL backend requires psycopg; add psycopg[binary] to the function image") from exc
        if not config["postgres_dsn"]:
            raise RuntimeError("JIT_POSTGRES_DSN is required when JIT_DB_BACKEND=postgresql")
        validate_identifier(config["postgres_schema"], "JIT_POSTGRES_SCHEMA")
        raw_conn = psycopg.connect(postgres_dsn(config), row_factory=dict_row)
        raw_conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(config["postgres_schema"])))
        conn = PostgresConnection(raw_conn)
    else:
        db_path = Path(config["db_path"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    if not app_config()["auto_init_db"]:
        return
    with connect() as conn:
        if app_config()["db_backend"] == "postgresql":
            raise RuntimeError("Run database/postgresql_schema.sql during deployment and set JIT_AUTO_INIT_DB=false for PostgreSQL")
        conn.executescript(
            """
            create table if not exists tenancy_catalog (
              tenancy_ocid text primary key,
              display_name text not null,
              is_parent integer not null,
              lifecycle_state text not null,
              home_region text,
              last_seen_at text not null,
              catalog_status text not null
            );

            create table if not exists group_catalog (
              group_ocid text primary key,
              group_name text not null,
              tenancy_ocid text not null,
              description text,
              access_template text,
              policy_summary text,
              is_default integer not null,
              active integer not null,
              last_seen_at text not null
            );

            create table if not exists app_user (
              email text primary key,
              display_name text not null,
              password_hash text,
              auth_source text not null,
              role text not null default 'requester',
              status text not null default 'active',
              verification_token text,
              verification_sent_at text,
              verified_at text,
              created_at text not null,
              last_login_at text
            );

            create table if not exists access_request (
              request_id integer primary key autoincrement,
              requester_email text not null,
              requester_name text not null,
              target_tenancy_ocid text not null,
              target_compartment_ocid text,
              access_type text not null,
              group_ocid text,
              target_tenancy_ocids text,
              requested_start_at text,
              requested_group_name text,
              service_family text,
              verb text,
              duration_minutes integer not null,
              justification text not null,
              generated_policy_statements text,
              jira_issue_key text,
              status text not null,
              approved_by text,
              approved_at text,
              expires_at text,
              revoked_at text,
              extension_count integer not null default 0,
              extension_status text,
              extension_minutes integer,
              extension_requested_by text,
              extension_requested_at text,
              extension_approved_by text,
              extension_approved_at text,
              extension_token text,
              provisioned_memberships text,
              revocation_errors text,
              warning_sent_at text,
              created_at text not null
            );

            create table if not exists audit_event (
              event_id integer primary key autoincrement,
              request_id integer,
              actor text,
              action text not null,
              target_tenancy_ocid text,
              payload_json text,
              result text not null,
              created_at text not null
            );
            """
        )
        ensure_columns(
            conn,
            "access_request",
            {
                "extension_count": "integer not null default 0",
                "extension_status": "text",
                "extension_minutes": "integer",
                "extension_requested_by": "text",
                "extension_requested_at": "text",
                "extension_approved_by": "text",
                "extension_approved_at": "text",
                "extension_token": "text",
                "target_tenancy_ocids": "text",
                "requested_start_at": "text",
                "provisioned_memberships": "text",
                "revocation_errors": "text",
                "warning_sent_at": "text",
            },
        )
        ensure_columns(
            conn,
            "app_user",
            {
                "display_name": "text not null default ''",
                "password_hash": "text",
                "auth_source": "text not null default 'builtin'",
                "role": "text not null default 'requester'",
                "status": "text not null default 'active'",
                "verification_token": "text",
                "verification_sent_at": "text",
                "verified_at": "text",
                "created_at": "text not null default ''",
                "last_login_at": "text",
            },
        )


def ensure_columns(conn, table_name, columns):
    existing = {row["name"] for row in conn.execute(f"pragma table_info({table_name})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"alter table {table_name} add column {name} {definition}")


def row_to_dict(row, include_secrets=False):
    if row is None:
        return None
    item = {key: serialize_value(row[key]) for key in row.keys()}
    if "generated_policy_statements" in item:
        item["generated_policy_statements"] = loads(item["generated_policy_statements"], [])
    for key in ("target_tenancy_ocids", "provisioned_memberships", "revocation_errors"):
        if key in item:
            item[key] = loads(item[key], [])
    if not include_secrets:
        item.pop("extension_token", None)
    return item


def audit(conn, action, result="ok", request_id=None, actor=None, target_tenancy_ocid=None, payload=None):
    conn.execute(
        """
        insert into audit_event(request_id, actor, action, target_tenancy_ocid, payload_json, result, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (request_id, actor, action, target_tenancy_ocid, dumps(payload), result, utc_now()),
    )
