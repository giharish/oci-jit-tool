import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import authenticated_actor
from app.function_runtime import error_response, read_payload, response
from app.handlers import preview_policy


def handler(ctx, data: io.BytesIO = None):
    try:
        payload = read_payload(data)
        actor = authenticated_actor(ctx)
        return response(ctx, 200, preview_policy(payload, actor))
    except Exception as exc:
        return error_response(ctx, exc)
