from .config import app_config, role_for


BLOCKED_POLICIES = {
    ("manage", "all-resources", "tenancy"),
    ("manage", "users", "tenancy"),
    ("manage", "groups", "tenancy"),
    ("manage", "policies", "tenancy"),
}


def validate_custom_access(payload, actor_email):
    allowlist = app_config()["service_allowlist"]
    verb = payload.get("verb")
    service_family = payload.get("service_family")
    scope_type = payload.get("scope_type", "compartment")
    role = role_for(actor_email)

    if service_family not in allowlist:
        raise ValueError(f"{service_family} is not in the service allowlist")
    if verb not in allowlist[service_family]["verbs"]:
        raise ValueError(f"{verb} is not allowed for {service_family}")
    if (verb, service_family, scope_type) in BLOCKED_POLICIES:
        raise ValueError("Blocked high-risk policy request: manage all-resources in tenancy")
    if scope_type == "tenancy" and role != "admin":
        raise ValueError("Tenancy-level custom access requires an administrator")
    if verb in allowlist[service_family].get("admin_required_for", []) and role != "admin":
        raise ValueError(f"{verb} access to {service_family} requires an administrator")
    if scope_type == "compartment" and not payload.get("target_compartment_ocid"):
        raise ValueError("Compartment-scoped custom access requires target_compartment_ocid")


def generate_policy_statements(group_ocid, payload):
    verb = payload["verb"]
    service_family = payload["service_family"]
    scope_type = payload.get("scope_type", "compartment")
    if scope_type == "tenancy":
        return [f"Allow group id {group_ocid} to {verb} {service_family} in tenancy"]
    compartment_ocid = payload["target_compartment_ocid"]
    return [f"Allow group id {group_ocid} to {verb} {service_family} in compartment id {compartment_ocid}"]

