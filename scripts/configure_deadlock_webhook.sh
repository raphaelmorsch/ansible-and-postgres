#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-databases}"
URL="${1:-}"

if [[ -z "${URL}" ]]; then
  echo "Usage: $0 <eda_webhook_url>" >&2
  exit 1
fi

echo "Configuring app-delta-deadlock with EDA webhook URL"
oc -n "$NS" set env deployment/app-delta-deadlock EDA_WEBHOOK_URL="$URL" APP_NAMESPACE="$NS"
oc -n "$NS" rollout status deployment/app-delta-deadlock --timeout=300s
echo "Done."
