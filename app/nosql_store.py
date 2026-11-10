"""Low-level OCI NoSQL access. The JIT lifecycle is not wired to this module yet."""
import re


TABLE_KEYS = {
    "tenancies": "tenancy_ocid",
    "groups": "group_ocid",
    "users": "email",
    "requests": "request_id",
    "audit": "event_id",
}


class NoSQLConflictError(RuntimeError):
    pass


class NoSQLStore:
    def __init__(self, client, update_row_details, compartment_id, prefix):
        if not compartment_id:
            raise ValueError("NoSQL compartment OCID is required")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", prefix or ""):
            raise ValueError("Invalid NoSQL table prefix")
        self.client = client
        self.details = update_row_details
        self.compartment_id = compartment_id
        self.prefix = prefix

    def table_name(self, kind):
        if kind not in TABLE_KEYS:
            raise ValueError("Unknown NoSQL table kind")
        return f"{self.prefix}_{kind}"

    def get(self, kind, key):
        if not isinstance(key, str) or not key:
            raise ValueError("NoSQL row key must be a non-empty string")
        response = self.client.get_row(
            self.table_name(kind), [f"{TABLE_KEYS[kind]}:{key}"],
            compartment_id=self.compartment_id, consistency="ABSOLUTE",
        )
        value = response.data.value if response.data is not None else None
        if value is None:
            return None, None
        return value, self._etag(response)

    def create(self, kind, row):
        self._validate_row(kind, row)
        request = self.details(compartment_id=self.compartment_id, value=row, option="IF_ABSENT")
        response = self.client.update_row(self.table_name(kind), request)
        if not response.data.version:
            raise NoSQLConflictError("NoSQL row already exists or write was rejected")
        return self._etag(response)

    def replace(self, kind, row, version):
        self._validate_row(kind, row)
        if not version:
            raise ValueError("A row version is required for replacement")
        request = self.details(compartment_id=self.compartment_id, value=row)
        response = self.client.update_row(self.table_name(kind), request, if_match=version)
        if not response.data.version:
            raise NoSQLConflictError("NoSQL row changed or write was rejected")
        return self._etag(response)

    @staticmethod
    def _etag(response):
        etag = response.headers.get("etag") or response.headers.get("ETag")
        if not etag:
            raise RuntimeError("NoSQL row response has no ETag; refusing unsafe updates")
        return etag

    def _validate_row(self, kind, row):
        self.table_name(kind)
        if not isinstance(row, dict) or not isinstance(row.get(TABLE_KEYS[kind]), str) or not row[TABLE_KEYS[kind]]:
            raise ValueError("NoSQL row must contain its non-empty string primary key")


def create_nosql_store(config):
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("OCI Python SDK is required for NoSQL access") from exc

    region = config.get("nosql_region")
    compartment = config.get("nosql_compartment_id")
    prefix = config.get("nosql_table_prefix")
    if not region or not compartment:
        raise ValueError("NoSQL region and compartment OCID are required")
    mode = config.get("oci_auth_mode")
    if mode == "resource_principal":
        signer = oci.auth.signers.get_resource_principals_signer()
        client = oci.nosql.NosqlClient({"region": region}, signer=signer)
    elif mode == "api_key":
        sdk_config = oci.config.from_file(config["oci_config_file"], config["oci_profile"])
        sdk_config["region"] = region
        client = oci.nosql.NosqlClient(sdk_config)
    else:
        raise ValueError("NoSQL authentication must be resource_principal or api_key")
    return NoSQLStore(client, oci.nosql.models.UpdateRowDetails, compartment, prefix)
