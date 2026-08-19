#!/usr/bin/env python3
"""Configure AAP deadlock response workflow and (when possible) EDA activation."""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SSL_CONTEXT = ssl._create_unverified_context()

AAP_URL = os.environ.get(
    "AAP_URL", "https://aap-aap.apps.cluster-d796h.dyn.redhatworkshops.io"
).rstrip("/")
CTRL_API = f"{AAP_URL}/api/controller/v2"
EDA_API = f"{AAP_URL}/api/eda/v1"
USERNAME = os.environ.get("AAP_USERNAME", "admin")
PASSWORD = os.environ.get("AAP_PASSWORD", "083RpsIxThJl")
ORG_NAME = os.environ.get("AAP_ORG", "Default")
PROJECT_NAME = "postgresql-preventive-maintenance"
INVENTORY_NAME = "PostgreSQL Inventories"
EDA_PROJECT_URL = os.environ.get(
    "EDA_PROJECT_URL", "http://demo-git.demo-git.svc.cluster.local/ansible-and-postgres.git"
)
EDA_DE_IMAGE = os.environ.get(
    "EDA_DECISION_ENV_IMAGE", "quay.io/ansible/ansible-rulebook:latest"
)
WF_NAME = "WF — Resposta a Deadlock PostgreSQL"
EDA_ACTIVATION_NAME = "Activation — PostgreSQL Deadlock AutoResponse"


class Api:
    def __init__(self, base: str, user: str, password: str):
        self.base = base
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, data=None):
        url = path if path.startswith("http") else f"{self.base}{path}"
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            payload = raw
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"{method} {url} -> {exc.code}: {payload}") from exc

    def get(self, path: str):
        return self.request("GET", path)[1]

    def post(self, path: str, data):
        return self.request("POST", path, data)[1]

    def patch(self, path: str, data):
        return self.request("PATCH", path, data)[1]

    def delete(self, path: str):
        return self.request("DELETE", path)[1]

    def find_one(self, path: str, **params):
        qs = urllib.parse.urlencode(params)
        data = self.get(f"{path}?{qs}" if qs else path)
        results = data.get("results", [])
        return results[0] if results else None

    def upsert(self, collection: str, name: str, payload: dict):
        obj = self.find_one(collection, name=name)
        if obj:
            print(f"update {collection} {name} id={obj['id']}")
            return self.patch(f"{collection}{obj['id']}/", payload)
        print(f"create {collection} {name}")
        return self.post(collection, payload)


def ensure_deadlock_workflow(ctrl: Api):
    org = ctrl.find_one("/organizations/", name=ORG_NAME) or ctrl.get("/organizations/")[
        "results"
    ][0]
    project = ctrl.find_one("/projects/", name=PROJECT_NAME)
    inventory = ctrl.find_one("/inventories/", name=INVENTORY_NAME)
    if not project or not inventory:
        raise RuntimeError(
            "Projeto/inventário do AAP não encontrado. Execute setup_aap_workflow.py antes."
        )

    # Remove legacy shim (same name as workflow, but as Job Template).
    legacy_shim = ctrl.find_one("/job_templates/", name=WF_NAME)
    if legacy_shim and legacy_shim.get("description") == "EDA launcher shim":
        try:
            ctrl.delete(f"/job_templates/{legacy_shim['id']}/")
            print(f"removed legacy shim job template id={legacy_shim['id']}")
        except RuntimeError as exc:
            print(f"legacy shim delete warning: {exc}")

    ee = ctrl.find_one("/execution_environments/", name="Default execution environment")
    extra_vars = {
        "vault_base_url": "http://mock-vault.mock-vault.svc.cluster.local:8080",
        "itsm_base_url": "http://mock-itsm.mock-itsm.svc.cluster.local:8080",
        "pg_namespace": "databases",
    }

    deadlock_templates = [
        ("JT08 — Diagnosticar Deadlock", "playbooks/jt08_deadlock_diagnose.yml"),
        ("JT09 — Conter Aplicação no OpenShift", "playbooks/jt09_contain_application.yml"),
        ("JT10 — Remediar Deadlock no Banco", "playbooks/jt10_remediate_deadlock.yml"),
        ("JT11 — Validar Recuperação", "playbooks/jt11_validate_deadlock_recovery.yml"),
        ("JT12 — Encerrar Ticket de Deadlock", "playbooks/jt12_close_deadlock_ticket.yml"),
        ("JT06 — Coletar evidências", "playbooks/jt06_collect_evidence.yml"),
        ("JT07 — Atualizar ticket como pendente", "playbooks/jt07_mark_ticket_pending.yml"),
    ]

    # OpenShift credential for containment job.
    openshift_cred = ctrl.find_one("/credentials/", name="OpenShift Cluster Admin")
    if not openshift_cred:
        raise RuntimeError("Credencial 'OpenShift Cluster Admin' não encontrada.")

    jt_ids = {}
    for jt_name, playbook in deadlock_templates:
        payload = {
            "name": jt_name,
            "description": jt_name,
            "job_type": "run",
            "inventory": inventory["id"],
            "project": project["id"],
            "playbook": playbook,
            "extra_vars": json.dumps(extra_vars),
            "ask_variables_on_launch": False,
            "allow_simultaneous": False,
            "verbosity": 1,
        }
        if ee:
            payload["execution_environment"] = ee["id"]
        jt = ctrl.upsert("/job_templates/", jt_name, payload)
        jt_ids[jt_name] = jt["id"]
        if jt_name.startswith("JT09"):
            try:
                ctrl.post(f"/job_templates/{jt['id']}/credentials/", {"id": openshift_cred["id"]})
            except RuntimeError as exc:
                print(f"credential attach warning: {exc}")

    wf = ctrl.upsert(
        "/workflow_job_templates/",
        WF_NAME,
        {
            "name": WF_NAME,
            "description": (
                "Detecção de deadlock → diagnóstico → contenção OpenShift → "
                "aprovação humana → remediação → validação → fechamento."
            ),
            "organization": org["id"],
            "inventory": inventory["id"],
            "extra_vars": json.dumps(extra_vars),
            "ask_variables_on_launch": True,
            "survey_enabled": False,
            "allow_simultaneous": False,
        },
    )

    # Recreate workflow graph.
    for node in ctrl.get(f"/workflow_job_templates/{wf['id']}/workflow_nodes/")["results"]:
        try:
            ctrl.delete(f"/workflow_job_template_nodes/{node['id']}/")
        except RuntimeError as exc:
            print(f"node delete warning: {exc}")

    def add_node(jt_name: str):
        node = ctrl.post(
            f"/workflow_job_templates/{wf['id']}/workflow_nodes/",
            {"unified_job_template": jt_ids[jt_name], "identifier": jt_name},
        )
        print(f"node {jt_name} -> {node['id']}")
        return node

    def relate(parent_id: int, child_id: int, rel: str):
        ctrl.post(f"/workflow_job_template_nodes/{parent_id}/{rel}/", {"id": child_id})

    n1 = add_node("JT08 — Diagnosticar Deadlock")
    n2 = add_node("JT09 — Conter Aplicação no OpenShift")
    n3 = add_node("JT10 — Remediar Deadlock no Banco")
    n4 = add_node("JT11 — Validar Recuperação")
    n5 = add_node("JT12 — Encerrar Ticket de Deadlock")
    n6 = add_node("JT06 — Coletar evidências")
    n7 = add_node("JT07 — Atualizar ticket como pendente")

    n_approval = ctrl.post(
        f"/workflow_job_templates/{wf['id']}/workflow_nodes/",
        {"identifier": "Aprovação — ação destrutiva"},
    )
    ctrl.post(
        f"/workflow_job_template_nodes/{n_approval['id']}/create_approval_template/",
        {
            "name": "Aprovar ação destrutiva no PostgreSQL",
            "description": (
                "Aprovação humana obrigatória antes de executar término de sessões no banco."
            ),
            "timeout": 7200,
        },
    )
    approval_node = ctrl.get(f"/workflow_job_template_nodes/{n_approval['id']}/")

    relate(n1["id"], n2["id"], "success_nodes")
    relate(n1["id"], n6["id"], "failure_nodes")
    relate(n2["id"], approval_node["id"], "success_nodes")
    relate(n2["id"], n6["id"], "failure_nodes")
    relate(approval_node["id"], n3["id"], "success_nodes")
    relate(approval_node["id"], n6["id"], "failure_nodes")
    relate(n3["id"], n4["id"], "success_nodes")
    relate(n4["id"], n5["id"], "success_nodes")
    relate(n3["id"], n6["id"], "failure_nodes")
    relate(n4["id"], n6["id"], "failure_nodes")
    relate(n6["id"], n7["id"], "always_nodes")

    # Assign execution capacity.
    ig = ctrl.find_one("/instance_groups/", name="openshift-ee") or ctrl.find_one(
        "/instance_groups/", name="controlplane"
    )
    if ig:
        for jt_id in jt_ids.values():
            try:
                ctrl.post(f"/job_templates/{jt_id}/instance_groups/", {"id": ig["id"]})
            except RuntimeError as exc:
                print(f"instance group warning JT {jt_id}: {exc}")

    return {
        "deadlock_workflow_id": wf["id"],
        "deadlock_workflow_name": WF_NAME,
        "approval_node_id": approval_node["id"],
        "deadlock_job_templates": jt_ids,
    }


def ensure_eda_activation(eda: Api):
    org = eda.find_one("/organizations/", name=ORG_NAME) or eda.get("/organizations/")[
        "results"
    ][0]
    de = eda.upsert(
        "/decision-environments/",
        "DE — PostgreSQL Deadlock",
        {
            "name": "DE — PostgreSQL Deadlock",
            "description": "Decision environment for PostgreSQL deadlock event processing.",
            "image_url": EDA_DE_IMAGE,
            "organization_id": org["id"],
            "pull_policy": "always",
        },
    )
    proj = eda.upsert(
        "/projects/",
        "EDA Project — PostgreSQL Deadlock",
        {
            "name": "EDA Project — PostgreSQL Deadlock",
            "description": "Rulebooks for deadlock auto-response",
            "organization_id": org["id"],
            "url": EDA_PROJECT_URL,
            "scm_type": "git",
            "scm_branch": "main",
            "verify_ssl": True,
        },
    )

    # Wait project import to find rulebook.
    for _ in range(24):
        current = eda.get(f"/projects/{proj['id']}/")
        state = current.get("import_state")
        if state == "completed":
            break
        if state == "failed":
            return {
                "eda_status": "warning",
                "eda_project_id": proj["id"],
                "eda_import_state": state,
                "eda_import_error": current.get("import_error"),
            }
        time.sleep(5)

    rb = eda.find_one("/rulebooks/", name="postgres_deadlock_webhook.yml")
    if not rb:
        return {
            "eda_status": "warning",
            "eda_project_id": proj["id"],
            "eda_import_state": "completed",
            "eda_import_error": "rulebook postgres_deadlock_webhook.yml não encontrado",
        }

    cred = eda.upsert(
        "/eda-credentials/",
        "AAP EDA Credential",
        {
            "name": "AAP EDA Credential",
            "description": "Credential used by EDA to call AAP controller APIs.",
            "organization_id": org["id"],
            "credential_type_id": 4,
            "inputs": {
                "host": f"{AAP_URL}/api/controller/",
                "username": USERNAME,
                "password": PASSWORD,
                "verify_ssl": False,
                "request_timeout": "10",
            },
        },
    )

    act = eda.upsert(
        "/activations/",
        EDA_ACTIVATION_NAME,
        {
            "name": EDA_ACTIVATION_NAME,
            "description": "Receive deadlock webhook and launch controller workflow.",
            "is_enabled": True,
            "decision_environment_id": de["id"],
            "rulebook_id": rb["id"],
            "organization_id": org["id"],
            "restart_policy": "always",
            "log_level": "info",
            "eda_credentials": [cred["id"]],
        },
    )
    act_full = eda.get(f"/activations/{act['id']}/")
    return {
        "eda_status": "configured",
        "eda_project_id": proj["id"],
        "eda_rulebook_id": rb["id"],
        "eda_activation_id": act["id"],
        "eda_activation_status": act_full.get("status"),
        "eda_webhook_endpoint": act_full.get("endpoint_url") or act_full.get("url"),
    }


def main():
    ctrl = Api(CTRL_API, USERNAME, PASSWORD)
    eda = Api(EDA_API, USERNAME, PASSWORD)

    workflow_info = ensure_deadlock_workflow(ctrl)
    eda_info = ensure_eda_activation(eda)

    print(
        json.dumps(
            {
                **workflow_info,
                **eda_info,
                "workflow_console": f"{AAP_URL}/#/templates/workflow_job_template/{workflow_info['deadlock_workflow_id']}/details",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
