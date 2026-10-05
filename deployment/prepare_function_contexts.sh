#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_SOURCE="${1:-}"
OCI_CONFIG_DIR="${2:-}"

if [[ -n "$CERT_SOURCE" && ! -f "$CERT_SOURCE" ]]; then
  echo "Certificate file not found: $CERT_SOURCE" >&2
  exit 1
fi

if [[ -n "$OCI_CONFIG_DIR" && ! -d "$OCI_CONFIG_DIR" ]]; then
  echo "OCI config directory not found: $OCI_CONFIG_DIR" >&2
  exit 1
fi

for function_dir in "$ROOT_DIR"/functions/*; do
  [[ -d "$function_dir" ]] || continue
  [[ -f "$function_dir/func.py" ]] || continue

  rm -rf "$function_dir/app"
  cp -R "$ROOT_DIR/app" "$function_dir/app"
  cp "$ROOT_DIR/functions/requirements.txt" "$function_dir/requirements.txt"

  if [[ -n "$CERT_SOURCE" ]]; then
    mkdir -p "$function_dir/certs"
    cp "$CERT_SOURCE" "$function_dir/certs/dbsystem.pub"
  fi

  if [[ -n "$OCI_CONFIG_DIR" ]]; then
    rm -rf "$function_dir/.oci"
    mkdir -p "$function_dir/.oci"
    cp -R "$OCI_CONFIG_DIR"/. "$function_dir/.oci/"
  fi

  echo "Prepared $(basename "$function_dir")"
done

echo "Function build contexts are ready."
