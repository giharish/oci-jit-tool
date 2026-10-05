#!/usr/bin/env python3
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import parse_qs

from app.auth import login_builtin_user, register_builtin_user, verify_builtin_user_email, verify_session_token
from app.handlers import (
    approval_callback,
    approve_extension,
    create_request,
    list_catalog,
    list_requests,
    preview_policy,
    request_extension,
    revoke_request,
    run_expiry_scheduler,
    send_expiry_warnings,
    session_payload,
    sync_catalog,
)


APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR / "frontend"
PORT = int(os.environ.get("PORT", "8090"))


def authenticated_actor(headers):
    authorization = headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        actor = verify_session_token(authorization.split(" ", 1)[1].strip())
        if actor:
            return actor
    return (
        headers.get("X-Authenticated-User")
        or headers.get("X-Forwarded-Email")
        or os.environ.get("JIT_LOCAL_AUTH_EMAIL")
        or "anonymous"
    )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, format, *args):
        return

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_error(self, exc):
        status = 404 if isinstance(exc, KeyError) else 400 if isinstance(exc, ValueError) else 500
        self.send_json({"error": str(exc)}, status)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        actor = authenticated_actor(self.headers)
        try:
            if path == "/api/session":
                self.send_json(session_payload(actor))
                return
            if path == "/api/auth":
                self.send_json({"session": session_payload(actor)})
                return
            if path == "/api/catalog":
                self.send_json(list_catalog(actor))
                return
            if path == "/api/requests":
                requester_email = query.get("requester_email", [None])[0]
                self.send_json(list_requests(actor, requester_email=requester_email))
                return
            super().do_GET()
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self):
        path = urlparse(self.path).path
        payload = self.read_json()
        actor = authenticated_actor(self.headers)
        try:
            if path in ("/api/catalog/sync", "/api/catalog") and payload.get("action") == "sync":
                self.send_json(sync_catalog(actor))
                return
            if path == "/api/auth":
                action = payload.get("action", "session")
                if action == "register":
                    self.send_json(register_builtin_user(payload.get("email"), payload.get("display_name"), payload.get("password")), 201)
                elif action == "verify":
                    self.send_json(verify_builtin_user_email(payload.get("email"), payload.get("token")))
                elif action == "login":
                    self.send_json(login_builtin_user(payload.get("email"), payload.get("password")))
                else:
                    self.send_json({"session": session_payload(actor)})
                return
            if path == "/api/requests":
                if payload.get("action") == "list":
                    self.send_json(list_requests(actor))
                else:
                    self.send_json(create_request(payload, actor), 201)
                return
            if path == "/api/approval":
                self.send_json(approval_callback(payload, actor))
                return
            if path == "/api/policy/preview":
                self.send_json(preview_policy(payload, actor))
                return
            if path == "/api/revoke":
                self.send_json(revoke_request(int(payload["request_id"]), actor, payload.get("reason", "manual")))
                return
            if path == "/api/extension/request":
                self.send_json(request_extension(int(payload["request_id"]), payload, actor))
                return
            if path == "/api/extension/approve":
                self.send_json(approve_extension(int(payload["request_id"]), payload, actor))
                return
            if path == "/api/notifications/warnings":
                self.send_json(send_expiry_warnings(actor))
                return
            if path == "/api/expiry/run":
                self.send_json(run_expiry_scheduler(actor))
                return
            self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.handle_error(exc)


def main():
    print(f"JIT production app local simulator: http://127.0.0.1:{PORT}")
    print("Use Catalog > Sync Catalog before creating requests.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
