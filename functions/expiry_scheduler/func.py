import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import authenticated_actor
from app.function_runtime import error_response, response
from app.handlers import run_expiry_scheduler


def handler(ctx, data: io.BytesIO = None):
    try:
        return response(ctx, 200, run_expiry_scheduler(authenticated_actor(ctx, default="system")))
    except Exception as exc:
        return error_response(ctx, exc)
