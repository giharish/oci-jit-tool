import secrets
from datetime import datetime, timedelta, timezone

from .config import app_config, is_configured_admin, role_for
from .integrations import jira_gateway, notification_gateway, oci_gateway
from .policy import generate_policy_statements, validate_custom_access
from .store import audit, connect, dumps, init_db, parse_iso, row_to_dict, utc_now


REQUESTED = "requested"
SCHEDULED = "scheduled"
ACTIVE = "active"
REJECTED = "rejected"
REVOKED = "revoked"
FAILED = "failed"
REVOKE_FAILED = "revoke_failed"
EXTENSION_PENDING = "extension_pending"
EXTENSION_APPROVED = "extension_approved"
EXTENSION_REJECTED = "extension_rejected"
WARNING_THRESHOLD_MINUTES = 15

def max_extensions():
    return app_config()["max_extensions"]


def requested_tenancies(payload):
    values = payload.get("target_tenancy_ocids") or payload.get("target_tenancies") or []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",") if item.strip()]
    if not values and payload.get("target_tenancy_ocid"):
        values = [payload["target_tenancy_ocid"]]
    return list(dict.fromkeys(values))


def iso_z(dt):
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_requested_start(value):
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    parsed = parse_iso(str(value))
    if not parsed:
        raise ValueError("requested_start_at must be a valid ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def allowed_tenancies_for(email):
    mapping = app_config().get("user_tenancy_map") or {}
    values = mapping.get(str(email or "").lower()) or mapping.get(str(email or ""))
    if values is None:
        return None
    if values == "*" or values == ["*"]:
        return None
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",") if item.strip()]
    return set(values or [])


def validate_tenancy_assignment(requester_email, target_tenancies):
    allowed = allowed_tenancies_for(requester_email)
    if allowed is None:
        return
    requested = set(target_tenancies)
    blocked = sorted(requested - allowed)
    if blocked:
        raise ValueError(f"{requester_email} is not mapped to requested tenancy {blocked[0]}")


def ensure_init():
    init_db()


def require_authority(actor_email):
    role = role_for(actor_email)
    if role != "admin":
        raise ValueError(f"{actor_email} is not authorized for this action")
    return role


def admin_recipients():
    return sorted(email for email, role in app_config()["authorized_users"].items() if str(role).lower() == "admin")


def session_payload(actor_email="anonymous"):
    email = actor_email or "anonymous"
    display_name = None
    auth_source = None
    if email != "anonymous":
        try:
            with connect() as conn:
                row = conn.execute("select display_name, auth_source from app_user where email = ?", (email.lower(),)).fetchone()
                if row:
                    display_name = row["display_name"]
                    auth_source = row["auth_source"]
        except Exception:
            pass
    return {
        "email": email,
        "display_name": display_name,
        "auth_source": auth_source,
        "role": role_for(email),
        "is_authenticated": email != "anonymous",
        "is_admin": is_configured_admin(email),
    }


def list_catalog(actor_email=None):
    ensure_init()
    allowed = allowed_tenancies_for(actor_email) if actor_email and not is_configured_admin(actor_email) else None
    with connect() as conn:
        tenancies = [row_to_dict(row) for row in conn.execute("select * from tenancy_catalog order by is_parent desc, display_name")]
        groups = [row_to_dict(row) for row in conn.execute("select * from group_catalog where active order by tenancy_ocid, group_name")]
    if allowed is not None:
        tenancies = [item for item in tenancies if item["tenancy_ocid"] in allowed]
        groups = [item for item in groups if item["tenancy_ocid"] in allowed]
    return {
        "tenancies": tenancies,
        "groups": groups,
        "service_allowlist": app_config()["service_allowlist"],
        "access_policy": {
            "default_duration_minutes": app_config()["default_duration_minutes"],
            "max_duration_minutes": app_config()["max_duration_minutes"],
            "max_extensions": app_config()["max_extensions"],
            "allow_custom_policies": app_config()["allow_custom_policies"],
        },
    }


def sync_catalog(actor_email="system"):
    ensure_init()
    if actor_email != "system":
        require_authority(actor_email)
    gateway = oci_gateway()
    now = utc_now()
    tenancy_count = 0
    group_count = 0
    with connect() as conn:
        for tenancy in gateway.list_organization_tenancies():
            tenancy_count += 1
            conn.execute(
                """
                insert into tenancy_catalog(tenancy_ocid, display_name, is_parent, lifecycle_state, home_region, last_seen_at, catalog_status)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(tenancy_ocid) do update set
                  display_name = excluded.display_name,
                  is_parent = excluded.is_parent,
                  lifecycle_state = excluded.lifecycle_state,
                  home_region = excluded.home_region,
                  last_seen_at = excluded.last_seen_at,
                  catalog_status = excluded.catalog_status
                """,
                (
                    tenancy["tenancy_ocid"],
                    tenancy["display_name"],
                    bool(tenancy["is_parent"]),
                    tenancy["lifecycle_state"],
                    tenancy.get("home_region"),
                    now,
                    "synced",
                ),
            )
            for group in gateway.list_jit_groups(tenancy["tenancy_ocid"]):
                group_count += 1
                conn.execute(
                    """
                    insert into group_catalog(group_ocid, group_name, tenancy_ocid, description, access_template, policy_summary, is_default, active, last_seen_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(group_ocid) do update set
                      group_name = excluded.group_name,
                      description = excluded.description,
                      access_template = excluded.access_template,
                      policy_summary = excluded.policy_summary,
                      is_default = excluded.is_default,
                      active = excluded.active,
                      last_seen_at = excluded.last_seen_at
                    """,
                    (
                        group["group_ocid"],
                        group["group_name"],
                        group["tenancy_ocid"],
                        group.get("description"),
                        group.get("access_template"),
                        group.get("policy_summary"),
                        bool(group.get("is_default")),
                        bool(group.get("active")),
                        now,
                    ),
                )
        audit(conn, "catalog.synced", actor=actor_email, payload={"tenancies": tenancy_count, "groups": group_count})
    return {"tenancies": tenancy_count, "groups": group_count}


def list_requests(actor_email="anonymous", requester_email=None):
    ensure_init()
    with connect() as conn:
        if is_configured_admin(actor_email):
            requests = [row_to_dict(row) for row in conn.execute("select * from access_request order by created_at desc")]
            events = [row_to_dict(row) for row in conn.execute("select * from audit_event order by created_at desc, event_id desc limit 200")]
        else:
            visible_email = actor_email if actor_email != "anonymous" else None
            if not visible_email:
                return {"requests": [], "events": [], "session": session_payload(actor_email)}
            requests = [
                row_to_dict(row)
                for row in conn.execute(
                    "select * from access_request where requester_email = ? order by created_at desc",
                    (visible_email,),
                )
            ]
            request_ids = [item["request_id"] for item in requests]
            if request_ids:
                placeholders = ",".join("?" for _ in request_ids)
                events = [
                    row_to_dict(row)
                    for row in conn.execute(
                        f"select * from audit_event where request_id in ({placeholders}) order by created_at desc, event_id desc limit 200",
                        request_ids,
                    )
                ]
            else:
                events = []
    return {"requests": requests, "events": events, "session": session_payload(actor_email)}


def preview_policy(payload, actor_email):
    ensure_init()
    validate_custom_access(payload, actor_email)
    group_ocid = payload.get("group_ocid") or "ocid1.group.oc1..preview"
    return {"statements": generate_policy_statements(group_ocid, payload)}


def create_request(payload, actor_email):
    ensure_init()
    if actor_email == "anonymous" and not app_config()["allow_anonymous_requests"]:
        raise ValueError("Sign in before creating or monitoring access requests")
    if "duration_minutes" not in payload or not payload.get("duration_minutes"):
        payload["duration_minutes"] = app_config()["default_duration_minutes"]
    target_tenancies = requested_tenancies(payload)
    if not target_tenancies:
        raise ValueError("At least one target tenancy is required")
    required = ["requester_email", "requester_name", "access_type", "duration_minutes", "justification"]
    for field in required:
        if not str(payload.get(field, "")).strip():
            raise ValueError(f"Missing required field: {field}")
    if int(payload["duration_minutes"]) > app_config()["max_duration_minutes"]:
        raise ValueError(f"duration_minutes cannot exceed {app_config()['max_duration_minutes']}")
    if payload["access_type"] != "standard_group" and not app_config()["allow_custom_policies"]:
        raise ValueError("Only Terraform-managed predefined JIT groups are enabled; dynamic custom policies are disabled")
    if payload["access_type"] not in ("standard_group", "custom_policy"):
        raise ValueError("access_type must be standard_group or custom_policy")
    if payload["access_type"] == "standard_group" and not (payload.get("group_ocid") or payload.get("requested_group_name")):
        raise ValueError("standard_group requests require requested_group_name or group_ocid")
    if payload["access_type"] == "custom_policy":
        preview_policy(payload, actor_email)
    requester_email = payload["requester_email"].strip()
    if actor_email != "anonymous" and actor_email.lower() != requester_email.lower():
        raise ValueError("Authenticated requesters can submit access only for themselves")
    validate_tenancy_assignment(requester_email, target_tenancies)
    requested_start_at = normalize_requested_start(payload.get("requested_start_at"))

    with connect() as conn:
        cursor = conn.execute(
            """
            insert into access_request(
              requester_email, requester_name, target_tenancy_ocid, target_tenancy_ocids, requested_start_at, target_compartment_ocid, access_type,
              group_ocid, requested_group_name, service_family, verb, duration_minutes, justification,
              generated_policy_statements, status, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            returning request_id
            """,
            (
                requester_email,
                payload["requester_name"].strip(),
                target_tenancies[0],
                dumps(target_tenancies),
                iso_z(requested_start_at),
                payload.get("target_compartment_ocid"),
                payload["access_type"],
                payload.get("group_ocid"),
                payload.get("requested_group_name"),
                payload.get("service_family"),
                payload.get("verb"),
                int(payload["duration_minutes"]),
                payload["justification"].strip(),
                dumps(payload.get("generated_policy_statements", [])),
                REQUESTED,
                utc_now(),
            ),
        )
        request_id = cursor.fetchone()["request_id"]
        issue = jira_gateway().create_issue(request_id, payload)
        conn.execute("update access_request set jira_issue_key = ? where request_id = ?", (issue["issue_key"], request_id))
        audit(conn, "request.created", request_id=request_id, actor=actor_email, target_tenancy_ocid=target_tenancies[0], payload={**payload, "target_tenancy_ocids": target_tenancies})
        return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())


def provision_request(request_id, actor_email, allowed_statuses=(REQUESTED,), reason="approval"):
    ensure_init()
    gateway = oci_gateway()
    with connect() as conn:
        row = conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone()
        if not row:
            raise KeyError("Request not found")
        request = row_to_dict(row)
        if request["status"] not in allowed_statuses:
            raise ValueError(f"Access cannot be provisioned from status {request['status']}")
        if actor_email != "system" and actor_email.lower() == request["requester_email"].lower():
            raise ValueError("Separation of duties violation: requester cannot approve their own access")

        target_tenancies = request.get("target_tenancy_ocids") or [request["target_tenancy_ocid"]]
        group_ocid = request["group_ocid"]
        statements = request["generated_policy_statements"] or []
        if request["access_type"] == "custom_policy":
            raise ValueError("Dynamic custom policies are disabled; use Terraform-managed predefined JIT groups")

        memberships = []
        for tenancy_ocid in target_tenancies:
            resolved_group_ocid = group_ocid
            if not resolved_group_ocid or len(target_tenancies) > 1:
                group_row = conn.execute(
                    "select * from group_catalog where tenancy_ocid = ? and group_name = ? and active = ?",
                    (tenancy_ocid, request["requested_group_name"], True),
                ).fetchone()
                if not group_row:
                    raise ValueError(f"Predefined JIT group {request['requested_group_name']} was not found in tenancy {tenancy_ocid}")
                resolved_group_ocid = group_row["group_ocid"]
            result = gateway.attach_user_to_group(request["requester_email"], resolved_group_ocid, tenancy_ocid, request["requester_name"])
            memberships.append({"tenancy_ocid": tenancy_ocid, "group_ocid": resolved_group_ocid, **result})
        now = datetime.now(timezone.utc).replace(microsecond=0)
        requested_start = parse_iso(request.get("requested_start_at")) or now
        activation_start = requested_start if requested_start > now else now
        expires_at = activation_start + timedelta(minutes=int(request["duration_minutes"]))
        conn.execute(
            """
            update access_request
            set status = ?, approved_by = ?, approved_at = ?, expires_at = ?, group_ocid = ?, generated_policy_statements = ?,
                warning_sent_at = null, extension_token = ?, provisioned_memberships = ?, revocation_errors = ?
            where request_id = ?
            """,
            (
                ACTIVE,
                request.get("approved_by") or actor_email,
                request.get("approved_at") or utc_now(),
                iso_z(expires_at),
                memberships[0]["group_ocid"],
                dumps(statements),
                secrets.token_urlsafe(24),
                dumps(memberships),
                dumps([]),
                request_id,
            ),
        )
        audit(conn, "access.provisioned", request_id=request_id, actor=actor_email, target_tenancy_ocid=request["target_tenancy_ocid"], payload={"memberships": memberships, "reason": reason})
        return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())


def approval_callback(payload, actor_email):
    ensure_init()
    require_authority(actor_email)
    request_id = int(payload["request_id"])
    decision = payload.get("decision")
    if decision == "approved":
        with connect() as conn:
            row = conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone()
            if not row:
                raise KeyError("Request not found")
            request = row_to_dict(row)
            if request["status"] != REQUESTED:
                raise ValueError(f"Only requested access can be approved; current status is {request['status']}")
            if actor_email.lower() == request["requester_email"].lower():
                raise ValueError("Separation of duties violation: requester cannot approve their own access")
            requested_start = parse_iso(request.get("requested_start_at"))
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if requested_start and requested_start > now:
                expires_at = requested_start + timedelta(minutes=int(request["duration_minutes"]))
                conn.execute(
                    """
                    update access_request
                    set status = ?, approved_by = ?, approved_at = ?, expires_at = ?, warning_sent_at = null,
                        extension_token = ?, revocation_errors = ?
                    where request_id = ?
                    """,
                    (SCHEDULED, actor_email, utc_now(), iso_z(expires_at), secrets.token_urlsafe(24), dumps([]), request_id),
                )
                audit(
                    conn,
                    "access.scheduled",
                    request_id=request_id,
                    actor=actor_email,
                    target_tenancy_ocid=request["target_tenancy_ocid"],
                    payload={"requested_start_at": iso_z(requested_start), "expires_at": iso_z(expires_at)},
                )
                return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())
        return provision_request(request_id, actor_email)
    if decision == "rejected":
        with connect() as conn:
            row = conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone()
            if not row:
                raise KeyError("Request not found")
            conn.execute("update access_request set status = ?, approved_by = ?, approved_at = ? where request_id = ?", (REJECTED, actor_email, utc_now(), request_id))
            audit(conn, "access.rejected", request_id=request_id, actor=actor_email, target_tenancy_ocid=row["target_tenancy_ocid"], payload=payload)
            return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())
    raise ValueError("decision must be approved or rejected")


def revoke_request(request_id, actor_email="system", reason="manual"):
    ensure_init()
    gateway = oci_gateway()
    with connect() as conn:
        row = conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone()
        if not row:
            raise KeyError("Request not found")
        request = row_to_dict(row)
        if request["status"] not in (ACTIVE, REVOKE_FAILED):
            raise ValueError(f"Only active access can be revoked; current status is {request['status']}")
        if actor_email != "system":
            require_authority(actor_email)
        memberships = request.get("provisioned_memberships") or [{"tenancy_ocid": request["target_tenancy_ocid"], "group_ocid": request["group_ocid"]}]
        removed = []
        errors = []
        for membership in memberships:
            try:
                result = gateway.remove_user_from_group(request["requester_email"], membership["group_ocid"], membership["tenancy_ocid"])
                removed.append({**membership, **result})
            except Exception as exc:
                errors.append({"tenancy_ocid": membership["tenancy_ocid"], "group_ocid": membership["group_ocid"], "error": str(exc)})
        if errors:
            conn.execute("update access_request set status = ?, revocation_errors = ? where request_id = ?", (REVOKE_FAILED, dumps(errors), request_id))
            audit(conn, "access.revocation_failed", request_id=request_id, actor=actor_email, target_tenancy_ocid=request["target_tenancy_ocid"], payload={"reason": reason, "errors": errors}, result="error")
            recipients = admin_recipients()
            if recipients:
                notice = notification_gateway().send_revocation_failure_admin_notice(request, errors, recipients)
                audit(
                    conn,
                    "notification.revocation_failure_admin_sent",
                    request_id=request_id,
                    actor="system",
                    target_tenancy_ocid=request["target_tenancy_ocid"],
                    payload={"reason": reason, "errors": errors, **notice},
                )
            return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())
        conn.execute("update access_request set status = ?, revoked_at = ? where request_id = ?", (REVOKED, utc_now(), request_id))
        audit(conn, "access.revoked", request_id=request_id, actor=actor_email, target_tenancy_ocid=request["target_tenancy_ocid"], payload={"reason": reason, "removed": removed})
        return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())


def request_extension(request_id, payload, actor_email):
    ensure_init()
    minutes = int(payload.get("extension_minutes") or 0)
    if minutes < 1:
        raise ValueError("extension_minutes must be at least 1")
    with connect() as conn:
        row = conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone()
        if not row:
            raise KeyError("Request not found")
        request = row_to_dict(row)
        if request["status"] != ACTIVE:
            raise ValueError(f"Only active access can be extended; current status is {request['status']}")
        if actor_email == "anonymous":
            if payload.get("extension_token") != request.get("extension_token"):
                raise ValueError("Anonymous extension requests require the secure email extension token")
        elif actor_email.lower() != request["requester_email"].lower():
            raise ValueError("Only the requester can ask for an extension")
        if request["extension_count"] >= max_extensions():
            raise ValueError("Maximum extension count reached")
        if request["extension_status"] == EXTENSION_PENDING:
            raise ValueError("An extension request is already pending approval")
        if minutes > int(request["duration_minutes"]):
            raise ValueError("Extension minutes cannot exceed the originally requested duration")
        conn.execute(
            """
            update access_request
            set extension_status = ?, extension_minutes = ?, extension_requested_by = ?, extension_requested_at = ?
            where request_id = ?
            """,
            (EXTENSION_PENDING, minutes, actor_email, utc_now(), request_id),
        )
        audit(
            conn,
            "extension.requested",
            request_id=request_id,
            actor=actor_email,
            target_tenancy_ocid=request["target_tenancy_ocid"],
            payload={"extension_minutes": minutes, "remaining_extensions": max_extensions() - request["extension_count"]},
        )
        return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())


def approve_extension(request_id, payload, actor_email):
    ensure_init()
    require_authority(actor_email)
    decision = payload.get("decision", "approved")
    with connect() as conn:
        row = conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone()
        if not row:
            raise KeyError("Request not found")
        request = row_to_dict(row)
        if request["status"] != ACTIVE:
            raise ValueError(f"Only active access can be extended; current status is {request['status']}")
        if request["extension_status"] != EXTENSION_PENDING:
            raise ValueError("No pending extension request is available")
        if actor_email.lower() == request["requester_email"].lower():
            raise ValueError("Separation of duties violation: requester cannot approve their own extension")
        if decision == "rejected":
            conn.execute(
                """
                update access_request
                set extension_status = ?, extension_approved_by = ?, extension_approved_at = ?
                where request_id = ?
                """,
                (EXTENSION_REJECTED, actor_email, utc_now(), request_id),
            )
            audit(conn, "extension.rejected", request_id=request_id, actor=actor_email, target_tenancy_ocid=request["target_tenancy_ocid"], payload=payload)
            return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())
        if decision != "approved":
            raise ValueError("decision must be approved or rejected")
        minutes = int(request["extension_minutes"])
        if minutes > int(request["duration_minutes"]):
            raise ValueError("Extension minutes cannot exceed the originally requested duration")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        current_expiry = parse_iso(request["expires_at"]) or now
        base_expiry = current_expiry if current_expiry > now else now
        expires_at = (base_expiry + timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute(
            """
            update access_request
            set expires_at = ?, extension_count = extension_count + 1, extension_status = ?,
                extension_approved_by = ?, extension_approved_at = ?, warning_sent_at = null
            where request_id = ?
            """,
            (expires_at, EXTENSION_APPROVED, actor_email, utc_now(), request_id),
        )
        audit(
            conn,
            "extension.approved",
            request_id=request_id,
            actor=actor_email,
            target_tenancy_ocid=request["target_tenancy_ocid"],
            payload={"extension_minutes": minutes, "previous_expires_at": request["expires_at"], "new_expires_at": expires_at},
        )
        return row_to_dict(conn.execute("select * from access_request where request_id = ?", (request_id,)).fetchone())


def send_expiry_warnings(actor_email="system"):
    ensure_init()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    deadline = now + timedelta(minutes=WARNING_THRESHOLD_MINUTES)
    warnings = []
    gateway = notification_gateway()
    with connect() as conn:
        rows = conn.execute(
            """
            select * from access_request
            where status = ? and expires_at is not null and warning_sent_at is null and expires_at > ? and expires_at <= ?
            """,
            (ACTIVE, now.isoformat().replace("+00:00", "Z"), deadline.isoformat().replace("+00:00", "Z")),
        ).fetchall()
        for row in rows:
            request = row_to_dict(row, include_secrets=True)
            expires_at = parse_iso(request["expires_at"])
            minutes_left = max(0, int((expires_at - now).total_seconds() // 60))
            result = gateway.send_access_expiry_warning(request, minutes_left)
            conn.execute("update access_request set warning_sent_at = ? where request_id = ?", (utc_now(), request["request_id"]))
            audit(
                conn,
                "notification.expiry_warning_sent",
                request_id=request["request_id"],
                actor=actor_email,
                target_tenancy_ocid=request["target_tenancy_ocid"],
                payload={"minutes_left": minutes_left, **result},
            )
            warnings.append({"request_id": request["request_id"], "minutes_left": minutes_left, "recipient": request["requester_email"]})
    return {"warnings": warnings}


def run_expiry_scheduler(actor_email="system"):
    ensure_init()
    now = utc_now()
    activated = []
    revoked = []
    with connect() as conn:
        scheduled_rows = conn.execute(
            "select request_id from access_request where status = ? and requested_start_at <= ?",
            (SCHEDULED, now),
        ).fetchall()
    for row in scheduled_rows:
        activated.append(provision_request(row["request_id"], actor_email="system", allowed_statuses=(SCHEDULED,), reason="scheduled_start"))
    warnings = send_expiry_warnings(actor_email)["warnings"]
    with connect() as conn:
        rows = conn.execute("select request_id from access_request where status in (?, ?) and expires_at <= ?", (ACTIVE, REVOKE_FAILED, now)).fetchall()
    for row in rows:
        revoked.append(revoke_request(row["request_id"], actor_email=actor_email, reason="expiry"))
    return {"activated": activated, "warnings": warnings, "revoked": revoked}
