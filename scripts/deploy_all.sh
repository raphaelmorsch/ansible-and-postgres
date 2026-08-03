#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Deploying Mock Vault"
oc apply -f "$ROOT/openshift/mock-vault.yaml"

echo "==> Deploying Mock ITSM"
oc apply -f "$ROOT/openshift/mock-itsm.yaml"

echo "==> Deploying in-cluster Git server for AAP project source"
"$ROOT/scripts/deploy_git_server.sh"

echo "==> Waiting for mocks"
oc rollout status deployment/mock-vault -n mock-vault --timeout=180s
oc rollout status deployment/mock-itsm -n mock-itsm --timeout=180s

VAULT_ROUTE="$(oc get route mock-vault -n mock-vault -o jsonpath='{.spec.host}')"
ITSM_ROUTE="$(oc get route mock-itsm -n mock-itsm -o jsonpath='{.spec.host}')"
GIT_ROUTE="$(oc get route demo-git -n demo-git -o jsonpath='{.spec.host}')"

echo "Mock Vault: https://${VAULT_ROUTE}"
echo "Mock ITSM:  https://${ITSM_ROUTE}"
echo "Git SCM:    http://${GIT_ROUTE}/ansible-and-postgres.git"

echo "==> Seeding PostgreSQL demo data"
chmod +x "$ROOT/scripts/seed_postgres.sh"
"$ROOT/scripts/seed_postgres.sh"

echo "==> Configuring AAP workflow"
export SCM_URL="http://demo-git.demo-git.svc.cluster.local/ansible-and-postgres.git"
export VAULT_URL="http://mock-vault.mock-vault.svc.cluster.local:8080"
export ITSM_URL="http://mock-itsm.mock-itsm.svc.cluster.local:8080"
python3 "$ROOT/scripts/setup_aap_workflow.py"

echo "==> Smoke checks"
curl -sk "https://${VAULT_ROUTE}/secrets/postgresql-prod?mask=1" | python3 -m json.tool
curl -sk "https://${ITSM_ROUTE}/healthz" | python3 -m json.tool

cat <<EOF

Deploy complete.

URLs:
  AAP Gateway:  https://aap-aap.apps.cluster-bfd7z-1.dyn.redhatworkshops.io
  Mock Vault:   https://${VAULT_ROUTE}
  Mock ITSM UI: https://${ITSM_ROUTE}
  Git repo:     http://${GIT_ROUTE}/ansible-and-postgres.git

Next: open the workflow "WF — Manutenção Preventiva PostgreSQL" and launch it.
EOF
