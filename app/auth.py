import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import app_config, is_configured_admin, role_for
from .integrations import notification_gateway
from .store import connect, init_db, utc_now


AUTH_HEADER_KEYS = (
    "x-authenticated-user",
    "x-forwarded-email",
    "x-user-email",
    "x-jwt-claim-email",
)


def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _header_value(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _context_headers(ctx):
    if not ctx:
        return {}
    headers = {}
    for attr in ("Headers", "headers"):
        value = getattr(ctx, attr, None)
        if callable(value):
            value = value()
        if isinstance(value, dict):
            headers.update(value)
    normalized = {}
    for key, value in headers.items():
        normalized_key = str(key).lower()
        normalized[normalized_key] = value
        if normalized_key.startswith("fn-http-h-"):
            normalized[normalized_key.removeprefix("fn-http-h-")] = value
    return normalized


def _session_secret():
    secret = app_config()["session_secret"]
    if not secret:
        return None
    return secret.encode("utf-8")


def issue_session_token(email):
    secret = _session_secret()
    if not secret:
        raise ValueError("JIT_SESSION_SECRET must be configured before built-in login can issue sessions")
    now = int(time.time())
    payload = {
        "sub": email,
        "iat": now,
        "exp": now + app_config()["session_ttl_minutes"] * 60,
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_session_token(token):
    secret = _session_secret()
    if not secret or not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = _b64encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("sub")


def _bearer_token(headers):
    authorization = _header_value(headers.get("authorization"))
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def authenticated_actor(ctx=None, default="anonymous"):
    headers = _context_headers(ctx)
    token_actor = verify_session_token(_bearer_token(headers))
    if token_actor:
        return token_actor
    for key in AUTH_HEADER_KEYS:
        value = _header_value(headers.get(key))
        if value:
            return value
    return default


def _sha256_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _pbkdf2_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
    return hmac.compare_digest(_b64encode(actual), expected)


def hash_password(password, iterations=260000):
    salt = secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${_b64encode(digest)}"


def verify_password(password, user_record):
    def get_value(key):
        if hasattr(user_record, "get"):
            return user_record.get(key)
        try:
            return user_record[key]
        except (KeyError, IndexError, TypeError):
            return None

    password_hash = get_value("password_hash") or get_value("password_sha256")
    if not password_hash:
        return False
    if password_hash.startswith("pbkdf2_sha256$"):
        return _pbkdf2_password(password, password_hash)
    return hmac.compare_digest(_sha256_password(password), password_hash)


def normalize_email(email):
    return str(email or "").strip().lower()


def _session_for_email(email):
    email = normalize_email(email)
    display_name = None
    source = None
    try:
        init_db()
        with connect() as conn:
            row = conn.execute("select * from app_user where email = ?", (email,)).fetchone()
            if row:
                display_name = row["display_name"]
                source = row["auth_source"]
    except Exception:
        # Session creation should still work for trusted IdP/admin headers even if
        # the optional self-service user table has not been migrated yet.
        pass
    return {
        "email": email,
        "display_name": display_name,
        "auth_source": source,
        "role": role_for(email),
        "is_authenticated": True,
        "is_admin": is_configured_admin(email),
    }


def _db_user(email):
    try:
        init_db()
        with connect() as conn:
            return conn.execute("select * from app_user where email = ?", (normalize_email(email),)).fetchone()
    except Exception:
        return None


def _record_login(email):
    try:
        init_db()
        with connect() as conn:
            conn.execute("update app_user set last_login_at = ? where email = ?", (utc_now(), normalize_email(email)))
    except Exception:
        pass


def _row_value(row, key):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def email_domain(email):
    email = normalize_email(email)
    return email.rsplit("@", 1)[1] if "@" in email else ""


def require_allowed_domain(email):
    domains = app_config()["allowed_email_domains"]
    if not domains:
        return
    domain = email_domain(email)
    if domain not in domains:
        raise ValueError(f"Self-service registration is allowed only for: {', '.join(domains)}")


def verification_url(email, token):
    base_url = app_config()["portal_base_url"]
    if not base_url:
        return None
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}verify_email={email}&verification_token={token}"


def login_builtin_user(email, password):
    if app_config()["portal_auth_mode"] not in ("builtin", "mixed"):
        raise ValueError("Built-in login is not enabled")
    email = normalize_email(email)
    user = _db_user(email)
    if user and _row_value(user, "status") == "pending_verification":
        raise ValueError("Verify your email before signing in")
    if user and _row_value(user, "status") != "active":
        raise ValueError("User account is disabled")
    if user and _row_value(user, "auth_source") != "builtin":
        raise ValueError("Use the configured identity provider to sign in")
    if not user:
        users = {key.lower(): value for key, value in app_config()["builtin_users"].items()}
        user = users.get(email)
    if not user or not verify_password(password or "", user):
        raise ValueError("Invalid email or password")
    _record_login(email)
    return {"token": issue_session_token(email), "session": _session_for_email(email)}


def register_builtin_user(email, display_name, password):
    config = app_config()
    if config["portal_auth_mode"] not in ("builtin", "mixed"):
        raise ValueError("Built-in registration is not enabled")
    if not config["allow_self_registration"]:
        raise ValueError("Self-service registration is disabled")
    email = normalize_email(email)
    display_name = str(display_name or "").strip()
    if "@" not in email:
        raise ValueError("A valid email address is required")
    require_allowed_domain(email)
    if len(str(password or "")) < 10:
        raise ValueError("Password must be at least 10 characters")
    if not display_name:
        raise ValueError("Display name is required")
    verification_token = secrets.token_urlsafe(32)
    require_verification = config["require_email_verification"]
    status = "pending_verification" if require_verification else "active"
    verified_at = None if require_verification else utc_now()
    init_db()
    with connect() as conn:
        existing = conn.execute("select email from app_user where email = ?", (email,)).fetchone()
        if existing:
            raise ValueError("An account already exists for this email")
        conn.execute(
            """
            insert into app_user(
              email, display_name, password_hash, auth_source, role, status,
              verification_token, verification_sent_at, verified_at, created_at, last_login_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email,
                display_name,
                hash_password(password),
                "builtin",
                "requester",
                status,
                verification_token if require_verification else None,
                utc_now() if require_verification else None,
                verified_at,
                utc_now(),
                utc_now() if not require_verification else None,
            ),
        )
    if require_verification:
        result = notification_gateway().send_account_verification(
            email,
            display_name,
            verification_token,
            verification_url(email, verification_token),
        )
        return {
            "verification_required": True,
            "email": email,
            "message": "Verification email sent. Verify the account before signing in.",
            "notification": result,
        }
    return {"token": issue_session_token(email), "session": _session_for_email(email)}


def verify_builtin_user_email(email, token):
    if app_config()["portal_auth_mode"] not in ("builtin", "mixed"):
        raise ValueError("Built-in account verification is not enabled")
    email = normalize_email(email)
    token = str(token or "").strip()
    if not email or not token:
        raise ValueError("Email and verification token are required")
    init_db()
    with connect() as conn:
        row = conn.execute("select * from app_user where email = ?", (email,)).fetchone()
        if not row:
            raise ValueError("Account not found")
        if _row_value(row, "auth_source") != "builtin":
            raise ValueError("Use the configured identity provider to sign in")
        if _row_value(row, "status") == "disabled":
            raise ValueError("User account is disabled")
        expected = _row_value(row, "verification_token")
        if _row_value(row, "status") == "active" and not expected:
            return {"token": issue_session_token(email), "session": _session_for_email(email)}
        if not expected or not hmac.compare_digest(str(expected), token):
            raise ValueError("Invalid verification token")
        conn.execute(
            """
            update app_user
            set status = ?, verification_token = null, verified_at = ?, last_login_at = ?
            where email = ?
            """,
            ("active", utc_now(), utc_now(), email),
        )
    return {"token": issue_session_token(email), "session": _session_for_email(email)}
