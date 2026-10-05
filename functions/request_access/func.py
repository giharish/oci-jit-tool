import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import authenticated_actor
from app.function_runtime import error_response, read_payload, response
from app.handlers import create_request, list_requests


def handler(ctx, data: io.BytesIO = None):
    try:
        payload = read_payload(data)
        actor = authenticated_actor(ctx)
        if not payload or payload.get("action") == "list":
            return response(ctx, 200, list_requests(actor, requester_email=payload.get("requester_email")))
        return response(ctx, 201, create_request(payload, actor))
    except Exception as exc:
        return error_response(ctx, exc)
