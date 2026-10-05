import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import authenticated_actor, login_builtin_user, register_builtin_user, verify_builtin_user_email
from app.function_runtime import error_response, read_payload, response
from app.handlers import session_payload


def handler(ctx, data: io.BytesIO = None):
    try:
        payload = read_payload(data)
        action = payload.get("action", "session")
        if action == "register":
            return response(ctx, 201, register_builtin_user(payload.get("email"), payload.get("display_name"), payload.get("password")))
        if action == "verify":
            return response(ctx, 200, verify_builtin_user_email(payload.get("email"), payload.get("token")))
        if action == "login":
            return response(ctx, 200, login_builtin_user(payload.get("email"), payload.get("password")))
        if action == "logout":
            return response(ctx, 200, {"session": session_payload("anonymous")})
        return response(ctx, 200, {"session": session_payload(authenticated_actor(ctx))})
    except ValueError as exc:
        return response(ctx, 401, {"error": str(exc)})
    except Exception as exc:
        return error_response(ctx, exc)
