#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  cat >&2 <<'USAGE'
Usage:
  bash deployment/security_scan.sh <image-ref> [image-ref ...]

Example:
  bash deployment/security_scan.sh iad.ocir.io/<namespace>/jit-repo/request_access:0.0.1

The script scans OCI Function container images with Trivy and exits non-zero
when high or critical vulnerabilities are found.
USAGE
  exit 1
fi

if ! command -v trivy >/dev/null 2>&1; then
  echo "Trivy is required for image vulnerability scanning. Install it on the build host or CI runner." >&2
  exit 2
fi

for image_ref in "$@"; do
  echo "Scanning $image_ref"
  trivy image \
    --severity HIGH,CRITICAL \
    --ignore-unfixed \
    --exit-code 1 \
    "$image_ref"
done
