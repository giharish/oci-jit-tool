import json
import traceback

try:
    from fdk import response as fdk_response
except ImportError:
    fdk_response = None


def response(ctx, status, body):
    payload = json.dumps(body)
    headers = {"Content-Type": "application/json"}
    if fdk_response and ctx is not None:
        return fdk_response.Response(ctx, response_data=payload, headers=headers, status_code=status)
    return {"statusCode": status, "headers": headers, "body": payload}


def read_payload(data):
    if not data:
        return {}
    return json.loads(data.getvalue().decode("utf-8") or "{}")


def error_response(ctx, exc):
    traceback.print_exc()
    return response(ctx, 500, {"error_type": exc.__class__.__name__, "error": str(exc)})
