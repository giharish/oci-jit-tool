#!/usr/bin/env python3
"""Plan or provision an experimental JIT NoSQL schema using OCI CLI."""
import argparse
import json
import re
import subprocess
import sys


# Initial storage design. The NoSQL application adapter is not implemented yet.
TABLES = {
    "tenancies": ("tenancy_ocid STRING, document JSON, PRIMARY KEY (tenancy_ocid)", []),
    "groups": ("group_ocid STRING, tenancy_ocid STRING, group_name STRING, active BOOLEAN, document JSON, PRIMARY KEY (group_ocid)",
               [("tenancy_group", ["tenancy_ocid", "group_name", "active"])]),
    "users": ("email STRING, status STRING, document JSON, PRIMARY KEY (email)", []),
    "requests": ("request_id STRING, requester_email STRING, status STRING, requested_start_at STRING, expires_at STRING, created_at STRING, document JSON, PRIMARY KEY (request_id)",
                 [("requester", ["requester_email", "created_at"]),
                  ("expiry", ["status", "expires_at"]),
                  ("start", ["status", "requested_start_at"])]),
    "audit": ("event_id STRING, request_id STRING, created_at STRING, document JSON, PRIMARY KEY (event_id)",
              [("request", ["request_id", "created_at"])]),
}


def run_oci(args):
    result = subprocess.run(["oci", *args], check=True, text=True, capture_output=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--compartment-id", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--profile", default="DEFAULT")
    p.add_argument("--auth", choices=("api_key", "instance_principal", "resource_principal"), default="api_key")
    p.add_argument("--prefix", default="jit")
    p.add_argument("--read-units", type=int, default=10)
    p.add_argument("--write-units", type=int, default=10)
    p.add_argument("--storage-gb", type=int, default=1)
    p.add_argument("--apply", action="store_true", help="Create resources; without this flag only print the plan")
    return p


def main(argv=None, call=run_oci):
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", args.prefix):
        raise ValueError("Prefix must start with a letter and contain only letters, digits and underscores (max 101 characters).")
    if min(args.read_units, args.write_units, args.storage_gb) < 1:
        raise ValueError("Capacity values must be positive integers.")

    common = ["--region", args.region, "--auth", args.auth, "--output", "json"]
    if args.auth == "api_key":
        common += ["--profile", args.profile]
    names = {suffix: f"{args.prefix}_{suffix}" for suffix in TABLES}
    limits = {"maxReadUnits": args.read_units, "maxWriteUnits": args.write_units, "maxStorageInGBs": args.storage_gb}
    print("EXPERIMENTAL schema: the JIT Functions do not yet support OCI NoSQL.", flush=True)
    print("Target compartment:", args.compartment_id)
    print("Region:", args.region)
    print("Provisioned capacity PER table:", json.dumps(limits))
    for suffix, (columns, indexes) in TABLES.items():
        print(f"CREATE TABLE {names[suffix]} ({columns})")
        for index, fields in indexes:
            print(f"  INDEX {index}: {', '.join(fields)}")
    if not args.apply:
        print("Dry run only: no OCI calls or changes. Add --apply to create tables.")
        return 0

    result = call(["nosql", "table", "list", "--compartment-id", args.compartment_id, "--all", *common])
    data = result.get("data")
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in items):
        raise RuntimeError("Unexpected table-list response; refusing to create resources.")
    existing = {item["name"].lower() for item in items}
    conflicts = [name for name in names.values() if name.lower() in existing]
    if conflicts:
        raise RuntimeError("Existing table names found; no changes made: " + ", ".join(conflicts))

    print("Creating tables and indexes. Partial resources remain if an operation fails.", flush=True)
    for suffix, (columns, indexes) in TABLES.items():
        name = names[suffix]
        print(f"Creating table {name}...", flush=True)
        call(["nosql", "table", "create", "--compartment-id", args.compartment_id,
              "--name", name, "--ddl-statement", f"CREATE TABLE {name} ({columns})",
              "--table-limits", json.dumps(limits), "--wait-for-state", "SUCCEEDED", *common])
        for index, fields in indexes:
            print(f"Creating index {index} on {name}...", flush=True)
            keys = [{"columnName": field} for field in fields]
            call(["nosql", "index", "create", "--compartment-id", args.compartment_id,
                  "--table-name-or-id", name, "--index-name", index, "--keys", json.dumps(keys),
                  "--wait-for-state", "SUCCEEDED", *common])
    print("Created initial JIT tables and indexes. Application integration is not yet available.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"Provisioning failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        sys.exit(1)
