import json
import os
import shutil
import tempfile
import unittest
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.auth import authenticated_actor, hash_password, login_builtin_user, register_builtin_user, verify_builtin_user_email, verify_session_token
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
    sync_catalog,
)
from app.integrations import OciSdkGateway
from app.integrations import SmtpNotificationGateway
from app.store import connect, parse_iso
from app.store import postgres_dsn


class JitLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JIT_DB_PATH"] = os.path.join(self.tmp.name, "jit.db")
        os.environ["JIT_MOCK_CHILD_TENANCY_COUNT"] = "2"
        sync_catalog("iam.admin@customer.example")
        self.catalog = list_catalog()

    def tearDown(self):
        self.tmp.cleanup()

    def test_standard_group_request_approval_and_revoke(self):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        group = [item for item in self.catalog["groups"] if item["tenancy_ocid"] == child["tenancy_ocid"]][0]
        request = create_request(
            {
                "requester_email": "anika.singh@customer.example",
                "requester_name": "Anika Singh",
                "target_tenancy_ocid": child["tenancy_ocid"],
                "access_type": "standard_group",
                "group_ocid": group["group_ocid"],
                "requested_group_name": group["group_name"],
                "duration_minutes": 15,
                "justification": "Unit test",
            },
            "anika.singh@customer.example",
        )
        self.assertEqual(request["status"], "requested")
        with self.assertRaises(ValueError):
            approval_callback({"request_id": request["request_id"], "decision": "approved"}, "anika.singh@customer.example")
        active = approval_callback({"request_id": request["request_id"], "decision": "approved"}, "platform.owner@customer.example")
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["revocation_errors"], [])
        revoked = revoke_request(active["request_id"], "platform.owner@customer.example")
        self.assertEqual(revoked["status"], "revoked")

    def test_requester_cannot_impersonate_another_requester(self):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        group = [item for item in self.catalog["groups"] if item["tenancy_ocid"] == child["tenancy_ocid"]][0]
        with self.assertRaises(ValueError):
            create_request(
                {
                    "requester_email": "safe.backend@example.com",
                    "requester_name": "Safe Backend",
                    "target_tenancy_ocid": child["tenancy_ocid"],
                    "access_type": "standard_group",
                    "group_ocid": group["group_ocid"],
                    "requested_group_name": group["group_name"],
                    "duration_minutes": 15,
                    "justification": "Should fail",
                },
                "anika.singh@customer.example",
            )

    def test_custom_policy_preview_blocks_high_risk_tenancy_manage(self):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        with self.assertRaises(ValueError):
            preview_policy(
                {
                    "target_tenancy_ocid": child["tenancy_ocid"],
                    "service_family": "all-resources",
                    "verb": "manage",
                    "scope_type": "tenancy",
                },
                "iam.admin@customer.example",
            )

    def test_custom_policy_preview_uses_compartment_ocid(self):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        preview = preview_policy(
            {
                "target_tenancy_ocid": child["tenancy_ocid"],
                "target_compartment_ocid": "ocid1.compartment.oc1..example",
                "service_family": "object-family",
                "verb": "read",
                "scope_type": "compartment",
            },
            "anika.singh@customer.example",
        )
        self.assertIn("compartment id ocid1.compartment.oc1..example", preview["statements"][0])

    def active_standard_request(self, duration=15):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        group = [item for item in self.catalog["groups"] if item["tenancy_ocid"] == child["tenancy_ocid"]][0]
        request = create_request(
            {
                "requester_email": "anika.singh@customer.example",
                "requester_name": "Anika Singh",
                "target_tenancy_ocid": child["tenancy_ocid"],
                "access_type": "standard_group",
                "group_ocid": group["group_ocid"],
                "requested_group_name": group["group_name"],
                "duration_minutes": duration,
                "justification": "Extension test",
            },
            "anika.singh@customer.example",
        )
        return approval_callback({"request_id": request["request_id"], "decision": "approved"}, "platform.owner@customer.example")

    def test_warning_email_sent_when_less_than_15_minutes_left(self):
        active = self.active_standard_request(duration=15)
        soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with connect() as conn:
            conn.execute("update access_request set expires_at = ?, warning_sent_at = null where request_id = ?", (soon, active["request_id"]))
        result = send_expiry_warnings("system")
        self.assertEqual(len(result["warnings"]), 1)
        second = send_expiry_warnings("system")
        self.assertEqual(len(second["warnings"]), 0)
        visible = list_requests("anika.singh@customer.example")
        self.assertNotIn("extension_token", visible["requests"][0])

    def test_extension_adds_to_remaining_time_and_is_capped_at_two(self):
        active = self.active_standard_request(duration=30)
        original_expiry = parse_iso(active["expires_at"])
        requested = request_extension(active["request_id"], {"extension_minutes": 20}, "anika.singh@customer.example")
        self.assertEqual(requested["extension_status"], "extension_pending")
        approved = approve_extension(active["request_id"], {"decision": "approved"}, "platform.owner@customer.example")
        self.assertEqual(approved["extension_count"], 1)
        self.assertEqual(approved["extension_status"], "extension_approved")
        self.assertIsNone(approved["warning_sent_at"])
        self.assertEqual(parse_iso(approved["expires_at"]), original_expiry + timedelta(minutes=20))

        first_extension_expiry = parse_iso(approved["expires_at"])
        request_extension(active["request_id"], {"extension_minutes": 30}, "anika.singh@customer.example")
        approved = approve_extension(active["request_id"], {"decision": "approved"}, "platform.owner@customer.example")
        self.assertEqual(approved["extension_count"], 2)
        self.assertEqual(parse_iso(approved["expires_at"]), first_extension_expiry + timedelta(minutes=30))
        with self.assertRaises(ValueError):
            request_extension(active["request_id"], {"extension_minutes": 5}, "anika.singh@customer.example")

    def test_extension_cannot_exceed_original_duration(self):
        active = self.active_standard_request(duration=15)
        with self.assertRaises(ValueError):
            request_extension(active["request_id"], {"extension_minutes": 16}, "anika.singh@customer.example")

    def test_request_visibility_is_filtered_unless_admin(self):
        self.active_standard_request(duration=15)
        requester_view = list_requests("anika.singh@customer.example")
        self.assertEqual(len(requester_view["requests"]), 1)
        anonymous_view = list_requests("anonymous", requester_email="anika.singh@customer.example")
        self.assertEqual(len(anonymous_view["requests"]), 0)
        other_view = list_requests("safe.backend@example.com")
        self.assertEqual(len(other_view["requests"]), 0)
        admin_view = list_requests("iam.admin@customer.example")
        self.assertGreaterEqual(len(admin_view["requests"]), 1)

    def test_anonymous_request_creation_requires_authentication(self):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        group = [item for item in self.catalog["groups"] if item["tenancy_ocid"] == child["tenancy_ocid"]][0]
        with self.assertRaises(ValueError):
            create_request(
                {
                    "requester_email": "anika.singh@customer.example",
                    "requester_name": "Anika Singh",
                    "target_tenancy_ocid": child["tenancy_ocid"],
                    "access_type": "standard_group",
                    "group_ocid": group["group_ocid"],
                    "requested_group_name": group["group_name"],
                    "duration_minutes": 15,
                    "justification": "Should require sign-in",
                },
                "anonymous",
            )

    def test_multi_tenancy_request_provisions_and_revokes_each_membership(self):
        children = [item for item in self.catalog["tenancies"] if not item["is_parent"]]
        group_name = "JIT-Compute-Operator"
        request = create_request(
            {
                "requester_email": "anika.singh@customer.example",
                "requester_name": "Anika Singh",
                "target_tenancy_ocids": [child["tenancy_ocid"] for child in children],
                "access_type": "standard_group",
                "requested_group_name": group_name,
                "duration_minutes": 15,
                "justification": "Multi-tenancy validation",
            },
            "anika.singh@customer.example",
        )
        active = approval_callback({"request_id": request["request_id"], "decision": "approved"}, "platform.owner@customer.example")
        self.assertEqual(active["status"], "active")
        self.assertEqual(len(active["provisioned_memberships"]), len(children))
        revoked = revoke_request(active["request_id"], "platform.owner@customer.example")
        self.assertEqual(revoked["status"], "revoked")

    def test_user_tenancy_mapping_blocks_unassigned_tenancy(self):
        children = [item for item in self.catalog["tenancies"] if not item["is_parent"]]
        os.environ["JIT_USER_TENANCY_MAP"] = json.dumps({"anika.singh@customer.example": [children[0]["tenancy_ocid"]]})
        self.addCleanup(os.environ.pop, "JIT_USER_TENANCY_MAP", None)

        group_name = "JIT-Compute-Operator"
        request = create_request(
            {
                "requester_email": "anika.singh@customer.example",
                "requester_name": "Anika Singh",
                "target_tenancy_ocids": [children[0]["tenancy_ocid"]],
                "access_type": "standard_group",
                "requested_group_name": group_name,
                "duration_minutes": 15,
                "justification": "Mapped tenancy validation",
            },
            "anika.singh@customer.example",
        )
        self.assertEqual(request["target_tenancy_ocids"], [children[0]["tenancy_ocid"]])

        with self.assertRaises(ValueError):
            create_request(
                {
                    "requester_email": "anika.singh@customer.example",
                    "requester_name": "Anika Singh",
                    "target_tenancy_ocids": [children[1]["tenancy_ocid"]],
                    "access_type": "standard_group",
                    "requested_group_name": group_name,
                    "duration_minutes": 15,
                    "justification": "Blocked tenancy validation",
                },
                "anika.singh@customer.example",
            )

        filtered_catalog = list_catalog("anika.singh@customer.example")
        self.assertEqual([item["tenancy_ocid"] for item in filtered_catalog["tenancies"]], [children[0]["tenancy_ocid"]])

    def test_future_start_request_is_scheduled_then_activated_by_worker(self):
        child = [item for item in self.catalog["tenancies"] if not item["is_parent"]][0]
        group = [item for item in self.catalog["groups"] if item["tenancy_ocid"] == child["tenancy_ocid"]][0]
        future_start = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        request = create_request(
            {
                "requester_email": "anika.singh@customer.example",
                "requester_name": "Anika Singh",
                "target_tenancy_ocid": child["tenancy_ocid"],
                "access_type": "standard_group",
                "group_ocid": group["group_ocid"],
                "requested_group_name": group["group_name"],
                "requested_start_at": future_start,
                "duration_minutes": 15,
                "justification": "Scheduled start validation",
            },
            "anika.singh@customer.example",
        )

        scheduled = approval_callback({"request_id": request["request_id"], "decision": "approved"}, "platform.owner@customer.example")
        self.assertEqual(scheduled["status"], "scheduled")
        self.assertEqual(scheduled["provisioned_memberships"], [])

        with connect() as conn:
            past_start = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            conn.execute("update access_request set requested_start_at = ? where request_id = ?", (past_start, request["request_id"]))

        result = run_expiry_scheduler("system")
        self.assertEqual(len(result["activated"]), 1)
        self.assertEqual(result["activated"][0]["status"], "active")
        self.assertEqual(len(result["activated"][0]["provisioned_memberships"]), 1)

    def test_postgres_dsn_adds_sslrootcert_from_pem(self):
        dsn = postgres_dsn(
            {
                "postgres_dsn": "host=db.example.com dbname=jit user=jit_access_app sslmode=verify-full",
                "postgres_sslrootcert_path": None,
                "postgres_sslrootcert_pem": "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----",
            }
        )
        self.assertIn("sslrootcert=", dsn)
        self.assertIn("sslmode=verify-full", dsn)

    def test_postgres_dsn_strips_outer_smart_quotes(self):
        dsn = postgres_dsn(
            {
                "postgres_dsn": "“host=db.example.com dbname=jit user=jit_access_app sslmode=verify-full”",
                "postgres_sslrootcert_path": "/function/certs/dbsystem.pub",
                "postgres_sslrootcert_pem": None,
            }
        )
        self.assertTrue(dsn.startswith("host=db.example.com"))
        self.assertNotIn("“", dsn)

    def test_postgres_dsn_strips_escaped_outer_quotes(self):
        dsn = postgres_dsn(
            {
                "postgres_dsn": '\\"host=db.example.com dbname=jit user=jit_access_app sslmode=verify-full\\"',
                "postgres_sslrootcert_path": "/function/certs/dbsystem.pub",
                "postgres_sslrootcert_pem": None,
            }
        )
        self.assertTrue(dsn.startswith("host=db.example.com"))
        self.assertNotIn('\\"', dsn)

    def test_authenticated_actor_accepts_api_gateway_fn_header_prefix(self):
        class Context:
            def Headers(self):
                return {"Fn-Http-H-X-Authenticated-User": ["iam.admin@customer.example"]}

        self.assertEqual(authenticated_actor(Context()), "iam.admin@customer.example")

    def test_oci_user_lookup_requires_existing_identity(self):
        class Pagination:
            def list_call_get_all_results(self, *_args, **_kwargs):
                user = type("User", (), {"id": "ocid1.user.oc1..example", "name": "anika.singh@customer.example", "email": "anika.singh@customer.example"})()
                return type("Result", (), {"data": [user]})()

        class Oci:
            pagination = Pagination()

        class Client:
            def list_users(self, *_args, **_kwargs):
                return None

        gateway = object.__new__(OciSdkGateway)
        gateway.oci = Oci()
        gateway._identity_client = lambda _tenancy_ocid: Client()

        result = gateway.ensure_user("anika.singh@customer.example", "Anika Singh", "ocid1.tenancy.oc1..example")

        self.assertEqual(result["user_email"], "anika.singh@customer.example")

    def test_oci_user_lookup_fails_when_identity_is_missing(self):
        class Pagination:
            def list_call_get_all_results(self, *_args, **_kwargs):
                return type("Result", (), {"data": []})()

        class Oci:
            pagination = Pagination()

        class Client:
            def list_users(self, *_args, **_kwargs):
                return None

        gateway = object.__new__(OciSdkGateway)
        gateway.oci = Oci()
        gateway._identity_client = lambda _tenancy_ocid: Client()

        with self.assertRaises(ValueError):
            gateway.ensure_user("missing.user@customer.example", "Missing User", "ocid1.tenancy.oc1..example")

    def test_builtin_login_issues_signed_session_token(self):
        password_hash = hash_password("CorrectHorseBatteryStaple")
        os.environ["JIT_PORTAL_AUTH_MODE"] = "builtin"
        os.environ["JIT_SESSION_SECRET"] = "unit-test-secret"
        os.environ["JIT_BUILTIN_USERS"] = json.dumps(
            {"iam.admin@customer.example": {"password_hash": password_hash}}
        )
        self.addCleanup(os.environ.pop, "JIT_PORTAL_AUTH_MODE", None)
        self.addCleanup(os.environ.pop, "JIT_SESSION_SECRET", None)
        self.addCleanup(os.environ.pop, "JIT_BUILTIN_USERS", None)

        result = login_builtin_user("iam.admin@customer.example", "CorrectHorseBatteryStaple")
        self.assertEqual(result["session"]["email"], "iam.admin@customer.example")
        self.assertEqual(verify_session_token(result["token"]), "iam.admin@customer.example")

        class Context:
            def Headers(self):
                return {"Authorization": f"Bearer {result['token']}"}

        self.assertEqual(authenticated_actor(Context()), "iam.admin@customer.example")

    def test_self_service_registration_requires_allowed_domain_and_email_verification(self):
        os.environ["JIT_PORTAL_AUTH_MODE"] = "builtin"
        os.environ["JIT_SESSION_SECRET"] = "unit-test-secret"
        os.environ["JIT_ALLOWED_EMAIL_DOMAINS"] = "example.com"
        self.addCleanup(os.environ.pop, "JIT_PORTAL_AUTH_MODE", None)
        self.addCleanup(os.environ.pop, "JIT_SESSION_SECRET", None)
        self.addCleanup(os.environ.pop, "JIT_ALLOWED_EMAIL_DOMAINS", None)

        with self.assertRaises(ValueError):
            register_builtin_user("new.user@external.invalid", "New User", "CorrectHorseBatteryStaple")

        result = register_builtin_user("new.user@example.com", "New User", "CorrectHorseBatteryStaple")
        self.assertTrue(result["verification_required"])

        with self.assertRaises(ValueError):
            login_builtin_user("new.user@example.com", "CorrectHorseBatteryStaple")

        with connect() as conn:
            row = conn.execute("select verification_token, status from app_user where email = ?", ("new.user@example.com",)).fetchone()
        self.assertEqual(row["status"], "pending_verification")

        verified = verify_builtin_user_email("new.user@example.com", row["verification_token"])

        self.assertEqual(verified["session"]["email"], "new.user@example.com")
        self.assertEqual(verified["session"]["role"], "requester")
        self.assertEqual(verify_session_token(verified["token"]), "new.user@example.com")

    def test_smtp_notification_gateway_sends_verification_email(self):
        sent_messages = []

        class FakeSmtp:
            def __init__(self, host, port, timeout):
                self.host = host
                self.port = port
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def starttls(self):
                self.started_tls = True

            def login(self, username, password):
                self.username = username
                self.password = password

            def send_message(self, message):
                sent_messages.append(message)

        os.environ["JIT_NOTIFICATION_MODE"] = "smtp"
        os.environ["JIT_SMTP_HOST"] = "smtp.example.com"
        os.environ["JIT_SMTP_USERNAME"] = "smtp-user"
        os.environ["JIT_SMTP_PASSWORD"] = "smtp-password"
        os.environ["JIT_SMTP_SENDER"] = "jit-access@example.com"
        self.addCleanup(os.environ.pop, "JIT_NOTIFICATION_MODE", None)
        self.addCleanup(os.environ.pop, "JIT_SMTP_HOST", None)
        self.addCleanup(os.environ.pop, "JIT_SMTP_USERNAME", None)
        self.addCleanup(os.environ.pop, "JIT_SMTP_PASSWORD", None)
        self.addCleanup(os.environ.pop, "JIT_SMTP_SENDER", None)

        with patch("app.integrations.smtplib.SMTP", FakeSmtp):
            result = SmtpNotificationGateway().send_account_verification(
                "new.user@example.com",
                "New User",
                "verify-token",
                "https://portal.example.com/jit/?verify_email=new.user@example.com&verification_token=verify-token",
            )

        self.assertEqual(result["channel"], "smtp")
        self.assertEqual(result["recipient"], "new.user@example.com")
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Verification token: verify-token", sent_messages[0].get_content())

    def test_function_entrypoint_imports_in_packaged_layout(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            function_dir = Path(tmp) / "function"
            function_dir.mkdir()
            shutil.copy(root / "functions" / "catalog_sync" / "func.py", function_dir / "func.py")
            shutil.copytree(root / "app", function_dir / "app")
            spec = importlib.util.spec_from_file_location("packaged_catalog_sync", function_dir / "func.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.handler))


if __name__ == "__main__":
    unittest.main()
