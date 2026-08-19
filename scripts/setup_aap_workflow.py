#!/usr/bin/env python3
"""Configure AAP Controller resources for PostgreSQL preventive maintenance demo."""

from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SSL_CONTEXT = ssl._create_unverified_context()

AAP_URL = os.environ.get(
    "AAP_URL", "https://aap-aap.apps.cluster-d796h.dyn.redhatworkshops.io"
).rstrip("/")
API = f"{AAP_URL}/api/controller/v2"
USERNAME = os.environ.get("AAP_USERNAME", "admin")
PASSWORD = os.environ.get("AAP_PASSWORD", "083RpsIxThJl")
ORG_NAME = os.environ.get("AAP_ORG", "Default")
PROJECT_NAME = "postgresql-preventive-maintenance"
INVENTORY_NAME = "PostgreSQL Inventories"
PG_HOST = os.environ.get("PG_HOST", "postgresql.databases.svc.cluster.local")
PG_NAMESPACE = os.environ.get("PG_NAMESPACE", "databases")
SCM_URL = os.environ.get(
    "SCM_URL",
    "https://github.com/raphaelmorsch/ansible-and-postgres.git",
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


def openshift_token() -> tuple[str, str]:
    """Return (api_host, bearer_token) for OpenShift install playbooks."""
    token = os.environ.get("OPENSHIFT_TOKEN", "")
    host = os.environ.get("OPENSHIFT_API", "")
    if not token or not host:
        try:
            host = host or subprocess.check_output(
                ["oc", "whoami", "--show-server"], text=True
            ).strip()
            token = token or subprocess.check_output(
                ["oc", "whoami", "-t"], text=True
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise RuntimeError(
                "Set OPENSHIFT_TOKEN/OPENSHIFT_API or login with oc before setup"
            ) from exc
    return host, token


def ensure_openshift_credential(api: AAP, org_id: int, cred_types: dict):
    openshift_type = cred_types.get("OpenShift or Kubernetes API Bearer Token")
    if not openshift_type:
        openshift_type = cred_types.get("OpenShift or Kubernetes API")
    if not openshift_type:
        raise RuntimeError("OpenShift credential type not found in AAP")
    host, token = openshift_token()
    return api.upsert(
        "/credentials/",
        "OpenShift Cluster Admin",
        {
            "name": "OpenShift Cluster Admin",
            "description": "Cluster API token for PostgreSQL install playbooks",
            "organization": org_id,
            "credential_type": openshift_type["id"],
            "inputs": {
                "host": host,
                "bearer_token": token,
                "verify_ssl": False,
            },
        },
    )


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

    openshift_cred = ensure_openshift_credential(api, org["id"], cred_types)
    print(f"openshift credential id={openshift_cred['id']}")

    host_payload = {
        "name": PG_HOST,
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
        name=PG_HOST,
    )
    if existing_in_inv:
        host = api.patch(f"/hosts/{existing_in_inv['id']}/", host_payload)
    else:
        host = api.post("/hosts/", host_payload)
    api.post(f"/groups/{group['id']}/hosts/", {"id": host["id"]})
    print(f"host {host['id']} in group {group['id']}")

    for legacy_host in (
        "postgresql.postgresql.svc.cluster.local",
        "postgresql.databases.svc.cluster.local",
    ):
        if legacy_host == PG_HOST:
            continue
        old = api.find_one(f"/inventories/{inventory['id']}/hosts/", name=legacy_host)
        if old:
            try:
                api.delete(f"/hosts/{old['id']}/")
                print(f"removed legacy host {legacy_host}")
            except RuntimeError as exc:
                print(f"legacy host cleanup warning: {exc}")

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
        "pg_namespace": PG_NAMESPACE,
    }

    install_templates = [
        ("JT-Install — Namespace databases", "playbooks/jt_install_namespace.yml"),
        ("JT-Install — PostgreSQL", "playbooks/jt_install_postgresql.yml"),
    ]

    job_templates = [
        ("JT00 — Gerar Tuplas Mortas", "playbooks/jt00_generate_dead_tuples.yml"),
        ("JT01 — PostgreSQL Health Check", "playbooks/jt01_health_check.yml"),
        ("JT02 — Abrir Ticket de Manutenção", "playbooks/jt02_open_ticket.yml"),
        ("JT03 — Executar Manutenção PostgreSQL", "playbooks/jt03_execute_maintenance.yml"),
        ("JT04 — Validar Resultado", "playbooks/jt04_validate_result.yml"),
        ("JT05 — Atualizar e Encerrar Ticket", "playbooks/jt05_close_ticket.yml"),
        ("JT06 — Coletar evidências", "playbooks/jt06_collect_evidence.yml"),
        ("JT07 — Atualizar ticket como pendente", "playbooks/jt07_mark_ticket_pending.yml"),
    ]

    install_names = {name for name, _ in install_templates}
    jt_ids = {}
    for name, playbook in install_templates + job_templates:
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
        if name in install_names:
            try:
                api.post(
                    f"/job_templates/{jt['id']}/credentials/",
                    {"id": openshift_cred["id"]},
                )
            except RuntimeError as exc:
                print(f"openshift credential attach warning for {name}: {exc}")

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
    relate(n4, n6, "failure_nodes")
    relate(n6, n7, "always_nodes")

    install_wf_name = "WF — Instalar PostgreSQL (OpenShift)"
    install_wf = api.upsert(
        "/workflow_job_templates/",
        install_wf_name,
        {
            "name": install_wf_name,
            "description": (
                "Cria o namespace databases (se necessário), instala PostgreSQL "
                "e gera tuplas mortas para a demo de manutenção."
            ),
            "organization": org["id"],
            "inventory": inventory["id"],
            "extra_vars": json.dumps(extra_vars),
            "ask_variables_on_launch": False,
            "survey_enabled": False,
            "allow_simultaneous": False,
        },
    )
    install_nodes = api.get(
        f"/workflow_job_templates/{install_wf['id']}/workflow_nodes/"
    )["results"]
    for node in install_nodes:
        try:
            api.delete(f"/workflow_job_template_nodes/{node['id']}/")
        except RuntimeError as exc:
            print(f"install wf node delete warning: {exc}")

    def add_install_node(jt_name: str):
        payload = {
            "unified_job_template": jt_ids[jt_name],
            "identifier": jt_name,
        }
        return api.post(
            f"/workflow_job_templates/{install_wf['id']}/workflow_nodes/",
            payload,
        )

    i1 = add_install_node("JT-Install — Namespace databases")
    i2 = add_install_node("JT-Install — PostgreSQL")
    i3 = add_install_node("JT00 — Gerar Tuplas Mortas")
    relate(i1, i2, "success_nodes")
    relate(i2, i3, "success_nodes")

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

    # Prefer OpenShift container group when available (no execution nodes in this lab).
    ig = api.find_one("/instance_groups/", name="openshift-ee") or api.find_one(
        "/instance_groups/", name="controlplane"
    )
    if ig:
        for jt_id in jt_ids.values():
            try:
                api.post(f"/job_templates/{jt_id}/instance_groups/", {"id": ig["id"]})
            except RuntimeError as exc:
                print(f"instance group assign warning for JT {jt_id}: {exc}")
        print(f"job templates assigned to instance group {ig['name']} ({ig['id']})")

    print(
        json.dumps(
            {
                "workflow_id": wf["id"],
                "workflow_name": wf_name,
                "install_workflow_id": install_wf["id"],
                "install_workflow_name": install_wf_name,
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
