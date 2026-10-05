#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: bash deployment/deploy_functions_with_fn.sh <functions-app-name> [dbsystem.pub] [oci-config-dir]" >&2
  exit 1
fi

APP_NAME="$1"
CERT_SOURCE="${2:-}"
OCI_CONFIG_DIR="${3:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT_DIR/deployment/prepare_function_contexts.sh" "$CERT_SOURCE" "$OCI_CONFIG_DIR"

functions=(
  auth_session
  catalog_sync
  request_access
  approval_callback
  provision_access
  policy_builder
  revoke_access
  request_extension
  approve_extension
  notification_worker
  expiry_scheduler
)

for function_name in "${functions[@]}"; do
  echo "Deploying $function_name to $APP_NAME"
  (
    cd "$ROOT_DIR/functions/$function_name"
    fn -v deploy --app "$APP_NAME"
  )
done

echo "All functions deployed to $APP_NAME."
