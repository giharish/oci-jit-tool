#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: bash deployment/create_ocir_function_repos.sh <compartment-ocid> <repo-prefix> [oci-profile]" >&2
  exit 1
fi

COMPARTMENT_OCID="$1"
REPO_PREFIX="${2%/}"
OCI_PROFILE="${3:-}"

profile_args=()
if [[ -n "$OCI_PROFILE" ]]; then
  profile_args=(--profile "$OCI_PROFILE")
fi

repos=(
  auth-session-fn
  catalog-sync-fn
  request-access-fn
  approval-callback-fn
  provision-access-fn
  policy-builder-fn
  revoke-access-fn
  request-extension-fn
  approve-extension-fn
  notification-worker-fn
  expiry-scheduler-fn
)

for repo in "${repos[@]}"; do
  display_name="${REPO_PREFIX}/${repo}"
  echo "Ensuring OCIR repository ${display_name}"
  existing="$(
    oci artifacts container repository list \
      "${profile_args[@]}" \
      --compartment-id "$COMPARTMENT_OCID" \
      --display-name "$display_name" \
      --query 'data.items[0].id' \
      --raw-output 2>/dev/null || true
  )"
  if [[ -n "$existing" && "$existing" != "null" ]]; then
    echo "Already exists: ${display_name}"
    continue
  fi
  oci artifacts container repository create \
    "${profile_args[@]}" \
    --compartment-id "$COMPARTMENT_OCID" \
    --display-name "$display_name" \
    --is-public false >/dev/null
done

echo "OCIR repositories are ready."
