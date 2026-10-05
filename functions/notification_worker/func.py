import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import authenticated_actor
from app.function_runtime import error_response, response
from app.handlers import send_expiry_warnings


def handler(ctx, data: io.BytesIO = None):
    try:
        return response(ctx, 200, send_expiry_warnings(authenticated_actor(ctx, default="system")))
    except Exception as exc:
        return error_response(ctx, exc)
