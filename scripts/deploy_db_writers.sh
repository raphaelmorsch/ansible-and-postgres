#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NS="${NS:-databases}"

echo "==> Applying DB writer applications"
oc apply -f "$ROOT/openshift/db-writers.yaml"

echo "==> Waiting for deployments"
oc -n "$NS" rollout status deployment/app-alpha --timeout=300s
oc -n "$NS" rollout status deployment/app-beta --timeout=300s
oc -n "$NS" rollout status deployment/app-gamma-stress --timeout=300s
oc -n "$NS" rollout status deployment/app-delta-deadlock --timeout=300s

echo "==> Triggering write on each application"
oc -n "$NS" run curl-alpha --rm -i --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS "http://app-alpha:8080/write" >/dev/null
oc -n "$NS" run curl-beta --rm -i --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS "http://app-beta:8080/write" >/dev/null
oc -n "$NS" run curl-gamma --rm -i --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS "http://app-gamma-stress:8080/write" >/dev/null
oc -n "$NS" run curl-delta --rm -i --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS "http://app-delta-deadlock:8080/write" >/dev/null

ROUTE_HOST_STRESS="$(oc get route app-gamma-stress -n "$NS" -o jsonpath='{.spec.host}')"
ROUTE_HOST_DEADLOCK="$(oc get route app-delta-deadlock -n "$NS" -o jsonpath='{.spec.host}')"
echo
echo "Deploy complete."
echo "Stress app URL: https://${ROUTE_HOST_STRESS}"
echo "Button page opens at route root and calls /stress."
echo "Deadlock app URL: https://${ROUTE_HOST_DEADLOCK}"
echo "Button page opens at route root and calls /deadlock."
