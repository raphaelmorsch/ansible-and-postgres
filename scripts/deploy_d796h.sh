#!/usr/bin/env bash
# Bootstrap PostgreSQL maintenance demo on OpenShift cluster d796h.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export AAP_URL="${AAP_URL:-https://aap-aap.apps.cluster-d796h.dyn.redhatworkshops.io}"
export AAP_USERNAME="${AAP_USERNAME:-admin}"
export AAP_PASSWORD="${AAP_PASSWORD:-083RpsIxThJl}"
export PG_HOST="${PG_HOST:-postgresql.databases.svc.cluster.local}"
export PG_NAMESPACE="${PG_NAMESPACE:-databases}"
export SCM_URL="${SCM_URL:-http://demo-git.demo-git.svc.cluster.local/ansible-and-postgres.git}"
export VAULT_URL="${VAULT_URL:-http://mock-vault.mock-vault.svc.cluster.local:8080}"
export ITSM_URL="${ITSM_URL:-http://mock-itsm.mock-itsm.svc.cluster.local:8080}"

echo "==> OpenShift login (skip if already logged in)"
oc whoami >/dev/null 2>&1 || oc login https://api.cluster-d796h.dyn.redhatworkshops.io:6443 -u admin -p "${AAP_PASSWORD}" --insecure-skip-tls-verify=true

echo "==> Deploying Mock Vault"
oc apply -f "$ROOT/openshift/mock-vault.yaml"

echo "==> Deploying Mock ITSM"
oc apply -f "$ROOT/openshift/mock-itsm.yaml"

echo "==> Deploying in-cluster Git server for AAP project source"
"$ROOT/scripts/deploy_git_server.sh"

echo "==> Waiting for mocks"
oc rollout status deployment/mock-vault -n mock-vault --timeout=180s
oc rollout status deployment/mock-itsm -n mock-itsm --timeout=180s

echo "==> Configuring AAP container group (execution capacity)"
python3 "$ROOT/scripts/setup_container_group.py"

echo "==> Configuring AAP project, job templates and workflows"
python3 "$ROOT/scripts/setup_aap_workflow.py"

VAULT_ROUTE="$(oc get route mock-vault -n mock-vault -o jsonpath='{.spec.host}')"
ITSM_ROUTE="$(oc get route mock-itsm -n mock-itsm -o jsonpath='{.spec.host}')"
GIT_ROUTE="$(oc get route demo-git -n demo-git -o jsonpath='{.spec.host}')"

echo "==> Smoke checks"
curl -sk "https://${VAULT_ROUTE}/secrets/postgresql-prod?mask=1" | python3 -m json.tool
curl -sk "https://${ITSM_ROUTE}/healthz" | python3 -m json.tool

cat <<EOF

Deploy complete.

URLs:
  AAP Gateway:  ${AAP_URL}
  Mock Vault:   https://${VAULT_ROUTE}
  Mock ITSM UI: https://${ITSM_ROUTE}
  Git repo:     http://${GIT_ROUTE}/ansible-and-postgres.git

Next steps:
  1. Launch "WF — Instalar PostgreSQL (OpenShift)" to provision PostgreSQL in namespace ${PG_NAMESPACE}
  2. Launch "WF — Manutenção Preventiva PostgreSQL" for the preventive maintenance demo
EOF
