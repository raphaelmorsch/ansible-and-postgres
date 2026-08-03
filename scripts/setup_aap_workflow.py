#!/usr/bin/env python3
"""Configure AAP Controller resources for PostgreSQL preventive maintenance demo."""

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
    "AAP_URL", "https://aap-aap.apps.cluster-bfd7z-1.dyn.redhatworkshops.io"
).rstrip("/")
API = f"{AAP_URL}/api/controller/v2"
USERNAME = os.environ.get("AAP_USERNAME", "admin")
PASSWORD = os.environ.get("AAP_PASSWORD", "Mjk0NTYz_1")
ORG_NAME = os.environ.get("AAP_ORG", "Default")
PROJECT_NAME = "postgresql-preventive-maintenance"
INVENTORY_NAME = "PostgreSQL Inventories"
SCM_URL = os.environ.get(
    "SCM_URL",
    "http://demo-git.demo-git.svc.cluster.local/ansible-and-postgres.git",
)
VAULT_URL = os.environ.get(
    "VAULT_URL", "http://mock-vault.mock-vault.svc.cluster.local:8080"
)
ITSM_URL = os.environ.get(
    "ITSM_URL", "http://mock-itsm.mock-itsm.svc.cluster.local:8080"
)


class AAP:
    def __init__(self, base: str, user: str, password: str):
        self.base = base
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth_header = f"Basic {token}"

    def _request(self, method: str, path: str, data=None):
        if path.startswith("http"):
            url = path
        elif path.startswith("/api/"):
            # Related links from AAP already include /api/controller/v2/...
            url = f"{AAP_URL}{path}"
        else:
            url = f"{self.base}{path}"
        body = None
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.auth_header,
        }
        if data is not None:
            body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
                raw = resp.read().decode()
                if not raw:
                    return resp.status, {}
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            raise RuntimeError(f"{method} {url} -> {exc.code}: {payload}") from exc

    def delete(self, path: str):
        return self._request("DELETE", path)

    def get(self, path: str):
        return self._request("GET", path)[1]

    def post(self, path: str, data):
        return self._request("POST", path, data)[1]

    def patch(self, path: str, data):
        return self._request("PATCH", path, data)[1]

    def find_one(self, path: str, **params):
        qs = urllib.parse.urlencode(params)
        data = self.get(f"{path}?{qs}" if qs else path)
        results = data.get("results", [])
        return results[0] if results else None

    def upsert(self, collection: str, name: str, payload: dict, name_field="name"):
        existing = self.find_one(collection, **{name_field: name})
        if existing:
            print(f"update {collection} {name} (id={existing['id']})")
            return self.patch(f"{collection}{existing['id']}/", payload)
        print(f"create {collection} {name}")
        return self.post(collection, payload)


def wait_project(api: AAP, project_id: int, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        project = api.get(f"/projects/{project_id}/")
        status = project.get("status")
        print(f"project sync status: {status}")
        if status in {"successful", "ok"}:
            return project
        if status in {"failed", "error", "canceled"}:
            raise RuntimeError(f"project sync failed: {project}")
        time.sleep(5)
    raise TimeoutError("project sync timed out")


def ensure_controlplane_jobs(api: AAP):
    """Allow jobs to run when only control plane capacity exists."""
    control = api.find_one("/instance_groups/", name="controlplane")
    default = api.find_one("/instance_groups/", name="default")
    if not control:
        print("warning: controlplane instance group not found")
        return
    instances = api.get(control["related"]["instances"])
    instance_ids = [i["id"] for i in instances.get("results", [])]
    if default and instance_ids:
        # Associate control nodes to default IG if default has no capacity.
        default_full = api.get(f"/instance_groups/{default['id']}/")
        if (default_full.get("capacity") or 0) == 0:
            print("associating control plane instances to default instance group")
            for iid in instance_ids:
                try:
                    api.post(
                        f"/instance_groups/{default['id']}/instances/",
                        {"id": iid},
                    )
                except RuntimeError as exc:
                    print(f"  skip instance {iid}: {exc}")


def main():
    api = AAP(API, USERNAME, PASSWORD)
    me = api.get("/me/")
    print("authenticated as", me["results"][0]["username"])

    org = api.find_one("/organizations/", name=ORG_NAME)
    if not org:
        # Fall back to first org
        orgs = api.get("/organizations/")["results"]
        org = orgs[0]
    print("organization", org["id"], org["name"])

    ensure_controlplane_jobs(api)

    ee = api.find_one("/execution_environments/", name="Default execution environment")
    if not ee:
        ees = api.get("/execution_environments/")["results"]
        ee = ees[0] if ees else None
    print("execution environment", ee["id"] if ee else None, ee["name"] if ee else None)

    cred_types = {
        ct["name"]: ct
        for ct in api.get("/credential_types/?page_size=200")["results"]
    }
    machine_type = cred_types["Machine"]
    scm_type = cred_types.get("Source Control")

    # Source control credential not required for anonymous HTTP git.
    project = api.upsert(
        "/projects/",
        PROJECT_NAME,
        {
            "name": PROJECT_NAME,
            "description": "Manutenção preventiva PostgreSQL (demo AAP)",
            "organization": org["id"],
            "scm_type": "git",
            "scm_url": SCM_URL,
            "scm_branch": "main",
            "scm_update_on_launch": True,
            "allow_override": False,
        },
    )
    # Launch update
    try:
        api.post(f"/projects/{project['id']}/update/", {})
    except RuntimeError as exc:
        print("project update launch:", exc)
    project = wait_project(api, project["id"])

    inventory = api.upsert(
        "/inventories/",
        INVENTORY_NAME,
        {
            "name": INVENTORY_NAME,
            "description": "Inventário dos PostgreSQL da demonstração",
            "organization": org["id"],
        },
    )
    group = api.find_one(f"/inventories/{inventory['id']}/groups/", name="postgresql")
    if not group:
        group = api.post(
            "/groups/",
            {"name": "postgresql", "inventory": inventory["id"]},
        )
        print(f"created group postgresql id={group['id']}")

    host_payload = {
        "name": "postgresql.postgresql.svc.cluster.local",
        "inventory": inventory["id"],
        "variables": json.dumps(
            {
                "ansible_connection": "local",
                "pg_environment": "producao",
                "vault_secret": "postgresql-prod",
            }
        ),
    }
    existing_in_inv = api.find_one(
        f"/inventories/{inventory['id']}/hosts/",
        name="postgresql.postgresql.svc.cluster.local",
    )
    if existing_in_inv:
        host = api.patch(f"/hosts/{existing_in_inv['id']}/", host_payload)
    else:
        host = api.post("/hosts/", host_payload)
    api.post(f"/groups/{group['id']}/hosts/", {"id": host["id"]})
    print(f"host {host['id']} in group {group['id']}")

    # Machine credential unused for local connection, but keep for demo narrative.
    api.upsert(
        "/credentials/",
        "PostgreSQL Demo Placeholder",
        {
            "name": "PostgreSQL Demo Placeholder",
            "description": "Credenciais reais vêm do Mock Vault em runtime",
            "organization": org["id"],
            "credential_type": machine_type["id"],
            "inputs": {
                "username": "unused",
                "password": "unused",
            },
        },
    )

    extra_vars = {
        "vault_base_url": VAULT_URL,
        "itsm_base_url": ITSM_URL,
        "force_maintenance_window": True,
        "pg_max_connections_for_maintenance": 50,
    }

    job_templates = [
        ("JT01 — PostgreSQL Health Check", "playbooks/jt01_health_check.yml"),
        ("JT02 — Abrir Ticket de Manutenção", "playbooks/jt02_open_ticket.yml"),
        ("JT03 — Executar Manutenção PostgreSQL", "playbooks/jt03_execute_maintenance.yml"),
        ("JT04 — Validar Resultado", "playbooks/jt04_validate_result.yml"),
        ("JT05 — Atualizar e Encerrar Ticket", "playbooks/jt05_close_ticket.yml"),
        ("JT06 — Coletar evidências", "playbooks/jt06_collect_evidence.yml"),
        ("JT07 — Atualizar ticket como pendente", "playbooks/jt07_mark_ticket_pending.yml"),
    ]

    jt_ids = {}
    for name, playbook in job_templates:
        payload = {
            "name": name,
            "description": name,
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
        jt = api.upsert("/job_templates/", name, payload)
        jt_ids[name] = jt["id"]

    wf_name = "WF — Manutenção Preventiva PostgreSQL"
    wf = api.upsert(
        "/workflow_job_templates/",
        wf_name,
        {
            "name": wf_name,
            "description": (
                "Scheduler → Health Check → Ticket → Manutenção → "
                "Validação → Encerrar Ticket (com caminho de falha)"
            ),
            "organization": org["id"],
            "inventory": inventory["id"],
            "extra_vars": json.dumps(extra_vars),
            "ask_variables_on_launch": True,
            "survey_enabled": False,
            "allow_simultaneous": False,
        },
    )

    # Rebuild workflow nodes from scratch for idempotency.
    nodes = api.get(f"/workflow_job_templates/{wf['id']}/workflow_nodes/")["results"]
    for node in nodes:
        try:
            api.delete(f"/workflow_job_template_nodes/{node['id']}/")
            print(f"deleted workflow node {node['id']}")
        except RuntimeError as exc:
            print(f"failed deleting node {node['id']}: {exc}")

    def add_node(jt_name: str):
        payload = {
            "unified_job_template": jt_ids[jt_name],
            "identifier": jt_name,
        }
        node = api.post(
            f"/workflow_job_templates/{wf['id']}/workflow_nodes/",
            payload,
        )
        print(f"node {jt_name} -> {node['id']}")
        return node

    n1 = add_node("JT01 — PostgreSQL Health Check")
    n2 = add_node("JT02 — Abrir Ticket de Manutenção")
    n3 = add_node("JT03 — Executar Manutenção PostgreSQL")
    n4 = add_node("JT04 — Validar Resultado")
    n5 = add_node("JT05 — Atualizar e Encerrar Ticket")
    n6 = add_node("JT06 — Coletar evidências")
    n7 = add_node("JT07 — Atualizar ticket como pendente")

    def relate(parent, child, rel):
        # rel: success_nodes | failure_nodes | always_nodes
        api.post(
            f"/workflow_job_template_nodes/{parent['id']}/{rel}/",
            {"id": child["id"]},
        )
        print(f"{parent['identifier']} -{rel}-> {child['identifier']}")

    relate(n1, n2, "success_nodes")
    relate(n2, n3, "success_nodes")
    relate(n3, n4, "success_nodes")
    relate(n4, n5, "success_nodes")
    relate(n3, n6, "failure_nodes")
    relate(n6, n7, "always_nodes")

    # Optional weekly schedule (Sunday 02:00)
    schedule_name = "Weekly Sunday 02:00"
    existing_sched = api.find_one(
        f"/workflow_job_templates/{wf['id']}/schedules/",
        name=schedule_name,
    )
    schedule_payload = {
        "name": schedule_name,
        "rrule": "DTSTART:20260101T050000Z RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SU",
        "unified_job_template": wf["id"],
        "enabled": False,
        "extra_data": extra_vars,
    }
    try:
        if existing_sched:
            api.patch(f"/schedules/{existing_sched['id']}/", schedule_payload)
        else:
            api.post(f"/workflow_job_templates/{wf['id']}/schedules/", schedule_payload)
    except RuntimeError as exc:
        print(f"schedule warning (non-fatal): {exc}")

    # Ensure job templates can run on controlplane when no execution nodes exist.
    control = api.find_one("/instance_groups/", name="controlplane")
    if control:
        for jt_id in jt_ids.values():
            try:
                api.post(f"/job_templates/{jt_id}/instance_groups/", {"id": control["id"]})
            except RuntimeError as exc:
                print(f"instance group assign warning for JT {jt_id}: {exc}")

    print(
        json.dumps(
            {
                "workflow_id": wf["id"],
                "workflow_name": wf_name,
                "project_id": project["id"],
                "inventory_id": inventory["id"],
                "job_templates": jt_ids,
                "console": f"{AAP_URL}/#/templates/workflow_job_template/{wf['id']}/details",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
