import hashlib
import time

from .config import app_config


def stable_ocid(kind, *parts):
    digest = hashlib.sha1(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"ocid1.{kind}.oc1..{digest[:30]}"


class MockOciGateway:
    def list_organization_tenancies(self):
        config = app_config()
        parent_tenancy_ocid = config["parent_tenancy_ocid"] or "ocid1.tenancy.oc1..parent"
        items = [
            {
                "tenancy_ocid": parent_tenancy_ocid,
                "display_name": "Parent Management Tenancy",
                "is_parent": True,
                "lifecycle_state": "ACTIVE",
                "home_region": "ap-mumbai-1",
            }
        ]
        for index in range(1, config["mock_child_tenancy_count"] + 1):
            name = f"Child Tenancy {index:02d}"
            items.append(
                {
                    "tenancy_ocid": stable_ocid("tenancy", name),
                    "display_name": name,
                    "is_parent": False,
                    "lifecycle_state": "ACTIVE",
                    "home_region": "ap-mumbai-1",
                }
            )
        return items

    def list_jit_groups(self, tenancy_ocid):
        return [
            {
                "group_ocid": stable_ocid("group", tenancy_ocid, template["name"]),
                "group_name": template["name"],
                "tenancy_ocid": tenancy_ocid,
                "description": template["description"],
                "access_template": template["access_template"],
                "policy_summary": template["policy_summary"],
                "is_default": True,
                "active": True,
            }
            for template in app_config()["group_templates"]
        ]

    def ensure_group(self, tenancy_ocid, group_name, description):
        return {
            "group_ocid": stable_ocid("group", tenancy_ocid, group_name),
            "group_name": group_name,
            "tenancy_ocid": tenancy_ocid,
            "description": description,
        }

    def ensure_policy(self, tenancy_ocid, policy_name, statements):
        return {
            "policy_ocid": stable_ocid("policy", tenancy_ocid, policy_name, *statements),
            "policy_name": policy_name,
            "statements": statements,
        }

    def ensure_user(self, user_email, display_name, tenancy_ocid):
        return {"user_id": stable_ocid("user", tenancy_ocid, user_email), "user_email": user_email, "display_name": display_name}

    def attach_user_to_group(self, user_email, group_ocid, tenancy_ocid, display_name=None):
        return {"membership_id": stable_ocid("membership", user_email, group_ocid, tenancy_ocid)}

    def remove_user_from_group(self, user_email, group_ocid, tenancy_ocid):
        return {"removed": True, "user_email": user_email, "group_ocid": group_ocid, "tenancy_ocid": tenancy_ocid}


class OciSdkGateway:
    def __init__(self):
        try:
            import oci
            import oci.audit
            import oci.auth.signers
            import oci.identity
            import oci.pagination
            import oci.tenant_manager_control_plane
        except ImportError as exc:
            raise RuntimeError("OCI connector requires the oci Python SDK in the function image") from exc
        self.oci = oci
        self.config = app_config()
        self.oci_config, self.signer = self._auth()

    def _auth(self):
        mode = self.config["oci_auth_mode"]
        if mode == "resource_principal":
            signer = self.oci.auth.signers.get_resource_principals_signer()
            tenancy_ocid = (
                self.config["parent_tenancy_ocid"]
                or getattr(signer, "tenancy_id", None)
                or getattr(signer, "tenancy_ocid", None)
            )
            config = {"region": signer.region}
            if tenancy_ocid:
                config["tenancy"] = tenancy_ocid
            return config, signer
        if mode == "api_key":
            config = self.oci.config.from_file(self.config["oci_config_file"], self.config["oci_profile"])
            return config, None
        raise RuntimeError("JIT_OCI_AUTH_MODE must be resource_principal or api_key")

    def _client(self, client_class, config_override=None):
        config = dict(self.oci_config)
        if config_override:
            config.update(config_override)
        if self.signer:
            return client_class(config, signer=self.signer)
        return client_class(config)

    def _identity_client(self, tenancy_ocid=None):
        config_override = {"tenancy": tenancy_ocid} if tenancy_ocid and not self.signer else None
        return self._client(self.oci.identity.IdentityClient, config_override=config_override)

    def list_organization_tenancies(self):
        organization_ocid = self.config["organization_ocid"]
        if not organization_ocid:
            parent = self.config["parent_tenancy_ocid"] or self.oci_config.get("tenancy")
            if not parent:
                raise RuntimeError("Set JIT_PARENT_TENANCY_OCID for single-tenancy catalog, or JIT_ORGANIZATION_OCID for organization discovery")
            identity = self._identity_client(parent)
            tenancy = identity.get_tenancy(parent).data
            return [
                {
                    "tenancy_ocid": parent,
                    "display_name": tenancy.name,
                    "is_parent": True,
                    "lifecycle_state": "ACTIVE",
                    "home_region": self.oci_config.get("region"),
                }
            ]

        org_client = self._client(self.oci.tenant_manager_control_plane.OrganizationClient)
        tenancies = self.oci.pagination.list_call_get_all_results(org_client.list_organization_tenancies, organization_ocid).data.items
        return [
            {
                "tenancy_ocid": item.tenancy_id,
                "display_name": getattr(item, "name", None) or getattr(item, "tenancy_name", None) or item.tenancy_id,
                "is_parent": item.tenancy_id == self.config["parent_tenancy_ocid"],
                "lifecycle_state": getattr(item, "lifecycle_state", "ACTIVE"),
                "home_region": getattr(item, "home_region", None),
            }
            for item in tenancies
        ]

    def list_jit_groups(self, tenancy_ocid):
        identity = self._identity_client(tenancy_ocid)
        groups = self.oci.pagination.list_call_get_all_results(identity.list_groups, tenancy_ocid).data
        prefix = self.config["jit_group_prefix"]
        items = []
        for group in groups:
            if group.name.startswith(prefix):
                items.append(
                    {
                        "group_ocid": group.id,
                        "group_name": group.name,
                        "tenancy_ocid": tenancy_ocid,
                        "description": getattr(group, "description", None),
                        "access_template": group.name,
                        "policy_summary": "Terraform-managed JIT group and policy",
                        "is_default": True,
                        "active": getattr(group, "lifecycle_state", "ACTIVE") == "ACTIVE",
                    }
                )
        return items

    def ensure_user(self, user_email, display_name, tenancy_ocid):
        identity = self._identity_client(tenancy_ocid)
        users = self.oci.pagination.list_call_get_all_results(identity.list_users, tenancy_ocid).data
        for user in users:
            user_name = getattr(user, "name", None)
            user_primary_email = getattr(user, "email", None)
            if str(user_name or "").lower() == user_email.lower() or str(user_primary_email or "").lower() == user_email.lower():
                return {
                    "user_id": user.id,
                    "user_email": user_primary_email or user_name,
                    "display_name": getattr(user, "description", None),
                }
        raise ValueError(f"OCI IAM user {user_email} was not found in tenancy {tenancy_ocid}; create or sync the user before requesting JIT access")

    def _group_membership(self, identity, tenancy_ocid, user_id, group_ocid):
        memberships = self.oci.pagination.list_call_get_all_results(
            identity.list_user_group_memberships,
            tenancy_ocid,
            user_id=user_id,
            group_id=group_ocid,
        ).data
        return memberships[0] if memberships else None

    def attach_user_to_group(self, user_email, group_ocid, tenancy_ocid, display_name=None):
        identity = self._identity_client(tenancy_ocid)
        user = self.ensure_user(user_email, display_name or user_email, tenancy_ocid)
        existing = self._group_membership(identity, tenancy_ocid, user["user_id"], group_ocid)
        if existing:
            return {"membership_id": existing.id, "user_id": user["user_id"], "already_member": True}
        details = self.oci.identity.models.AddUserToGroupDetails(group_id=group_ocid, user_id=user["user_id"])
        response = identity.add_user_to_group(details)
        return {
            "membership_id": response.data.id,
            "user_id": user["user_id"],
            "opc_request_id": response.headers.get("opc-request-id"),
        }

    def remove_user_from_group(self, user_email, group_ocid, tenancy_ocid):
        identity = self._identity_client(tenancy_ocid)
        users = self.oci.pagination.list_call_get_all_results(identity.list_users, tenancy_ocid).data
        user = next(
            (
                item
                for item in users
                if str(getattr(item, "name", "") or "").lower() == user_email.lower()
                or str(getattr(item, "email", "") or "").lower() == user_email.lower()
            ),
            None,
        )
        if not user:
            return {"removed": True, "user_missing": True, "user_email": user_email, "group_ocid": group_ocid, "tenancy_ocid": tenancy_ocid}
        membership = self._group_membership(identity, tenancy_ocid, user.id, group_ocid)
        if not membership:
            return {"removed": True, "membership_missing": True, "user_email": user_email, "group_ocid": group_ocid, "tenancy_ocid": tenancy_ocid}
        response = identity.remove_user_from_group(membership.id)
        return {
            "removed": True,
            "membership_id": membership.id,
            "opc_request_id": response.headers.get("opc-request-id"),
            "user_email": user_email,
            "group_ocid": group_ocid,
            "tenancy_ocid": tenancy_ocid,
        }


class MockJiraGateway:
    def create_issue(self, request_id, payload):
        return {
            "issue_key": f"JIT-{request_id}",
            "issue_url": f"https://jira.example.invalid/browse/JIT-{request_id}",
        }

    def verify_callback(self, _headers, _payload):
        return True


class MockNotificationGateway:
    def send_account_verification(self, email, display_name, verification_token, verification_url=None):
        return {
            "message_id": stable_ocid("notification", "verify-account", email, verification_token),
            "channel": "email",
            "recipient": email,
            "subject": "Verify your OCI JIT access portal account",
            "verification_url_created": bool(verification_url),
            "display_name": display_name,
        }

    def send_access_expiry_warning(self, request, minutes_left):
        extension_token = request.get("extension_token")
        return {
            "message_id": stable_ocid("notification", request["request_id"], request["requester_email"], minutes_left),
            "channel": "email",
            "recipient": request["requester_email"],
            "subject": f"OCI JIT access expires in {minutes_left} minute(s)",
            "secure_extension_link_created": bool(extension_token),
        }

    def send_revocation_failure_admin_notice(self, request, errors, recipients):
        return {
            "message_id": stable_ocid("notification", "revocation-failed", request["request_id"], *recipients),
            "channel": "email",
            "recipients": recipients,
            "subject": f"OCI JIT revocation failed for request #{request['request_id']}",
            "error_count": len(errors),
        }


def oci_gateway():
    if app_config()["mode"] == "oci":
        return OciSdkGateway()
    return MockOciGateway()


def jira_gateway():
    return MockJiraGateway()


def notification_gateway():
    # Replace with OCI Email Delivery or OCI Notifications publisher in production.
    return MockNotificationGateway()


def request_expires_at(minutes):
    return int(time.time()) + int(minutes) * 60
