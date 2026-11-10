#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "Usage: bash deployment/check_oci_nosql.sh <compartment-ocid> <region> [api_key|resource_principal] [profile]" >&2
  exit 2
fi

compartment="$1"
region="$2"
auth_mode="${3:-api_key}"
profile="${4:-DEFAULT}"

if ! command -v oci >/dev/null 2>&1; then
  echo "OCI CLI is required for this read-only preflight." >&2
  exit 2
fi

args=(nosql table list --compartment-id "$compartment" --region "$region" --all)
case "$auth_mode" in
  api_key) args+=(--auth api_key --profile "$profile") ;;
  resource_principal) args+=(--auth resource_principal) ;;
  *) echo "Authentication must be api_key or resource_principal." >&2; exit 2 ;;
esac

oci "${args[@]}"
