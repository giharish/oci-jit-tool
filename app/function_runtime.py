import json
import traceback

from .config import app_config

try:
    from fdk import response as fdk_response
except ImportError:
    fdk_response = None


def response(ctx, status, body):
    payload = json.dumps(body)
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if fdk_response and ctx is not None:
        return fdk_response.Response(ctx, response_data=payload, headers=headers, status_code=status)
    return {"statusCode": status, "headers": headers, "body": payload}


def read_payload(data):
    if not data:
        return {}
    return json.loads(data.getvalue().decode("utf-8") or "{}")


def error_response(ctx, exc):
    if app_config()["debug_errors"]:
        traceback.print_exc()
    if isinstance(exc, KeyError):
        return response(ctx, 404, {"error_type": "NotFound", "error": str(exc)})
    if isinstance(exc, ValueError):
        return response(ctx, 400, {"error_type": "ValidationError", "error": str(exc)})
    return response(ctx, 500, {"error_type": "InternalServerError", "error": "Internal server error"})
