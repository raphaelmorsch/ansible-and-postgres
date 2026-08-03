#!/usr/bin/env python3
"""Create an OpenShift Container Group so AAP jobs can execute without EE nodes."""

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
    "AAP_URL", "https://aap-aap.apps.cluster-bfd7z-1.dyn.redhatworkshops.io"
).rstrip("/")
API = f"{AAP_URL}/api/controller/v2"
AUTH = base64.b64encode(
    f"{os.environ.get('AAP_USERNAME', 'admin')}:{os.environ.get('AAP_PASSWORD', 'Mjk0NTYz_1')}".encode()
).decode()
NS = "aap-ee"
SA = "aap-ee-runner"
IG_NAME = "openshift-ee"
CRED_NAME = "OpenShift EE Container Group"
JT_IDS = [12, 13, 14, 15, 16, 17, 18]


def oc(*args: str) -> str:
    return subprocess.check_output(["oc", *args], text=True).strip()


def api(method: str, path: str, data=None):
    url = path if path.startswith("http") else f"{API}{path}"
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Basic {AUTH}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        raise RuntimeError(f"{method} {url} -> {exc.code}: {raw[:800]}") from exc


def ensure_cluster_rbac():
    subprocess.check_call(
        ["oc", "create", "namespace", NS, "--dry-run=client", "-o", "yaml"],
        stdout=subprocess.PIPE,
    )
    subprocess.run(
        f"oc create namespace {NS} --dry-run=client -o yaml | oc apply -f -",
        shell=True,
        check=True,
    )
    manifest = f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {SA}
  namespace: {NS}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {SA}
  namespace: {NS}
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "pods/attach", "secrets", "configmaps"]
    verbs: ["create", "get", "list", "watch", "delete", "patch", "update"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {SA}
  namespace: {NS}
subjects:
  - kind: ServiceAccount
    name: {SA}
    namespace: {NS}
roleRef:
  kind: Role
  name: {SA}
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: {SA}-token
  namespace: {NS}
  annotations:
    kubernetes.io/service-account.name: {SA}
type: kubernetes.io/service-account-token
"""
    subprocess.run(["oc", "apply", "-f", "-"], input=manifest, text=True, check=True)
    subprocess.run(
        ["oc", "adm", "policy", "add-scc-to-user", "anyuid", "-z", SA, "-n", NS],
        check=False,
    )
    # wait for token
    for _ in range(30):
        try:
            token_b64 = oc(
                "get", "secret", f"{SA}-token", "-n", NS, "-o", "jsonpath={.data.token}"
            )
            if token_b64:
                return base64.b64decode(token_b64).decode(), oc("whoami", "--show-server")
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)
    raise RuntimeError("timed out waiting for SA token")


def main():
    token, host = ensure_cluster_rbac()
    print(f"OpenShift API host ready (token length={len(token)})")

    _, existing = api("GET", f"/credentials/?name={urllib.parse.quote(CRED_NAME)}")
    inputs = {"host": host, "bearer_token": token, "verify_ssl": False}
    if existing.get("count"):
        cred_id = existing["results"][0]["id"]
        print(f"updating credential id={cred_id}")
        api("PATCH", f"/credentials/{cred_id}/", {"inputs": inputs})
    else:
        print("creating credential")
        _, cred = api(
            "POST",
            "/credentials/",
            {
                "name": CRED_NAME,
                "organization": 1,
                "credential_type": 17,
                "inputs": inputs,
            },
        )
        cred_id = cred["id"]

    pod_spec = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"namespace": NS},
        "spec": {
            "serviceAccountName": SA,
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "image": "registry.redhat.io/ansible-automation-platform-26/ee-supported-rhel9:latest",
                    "name": "worker",
                    "args": ["ansible-runner", "worker", "--private-data-dir=/runner"],
                    "resources": {
                        "requests": {"cpu": "250m", "memory": "512Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                }
            ],
        },
    }
    payload = {
        "name": IG_NAME,
        "is_container_group": True,
        "credential": cred_id,
        "max_concurrent_jobs": 10,
        "max_forks": 50,
        "pod_spec_override": json.dumps(pod_spec),
    }
    _, igs = api("GET", f"/instance_groups/?name={IG_NAME}")
    if igs.get("count"):
        ig_id = igs["results"][0]["id"]
        print(f"updating instance group id={ig_id}")
        api("PATCH", f"/instance_groups/{ig_id}/", payload)
    else:
        print("creating instance group")
        _, ig = api("POST", "/instance_groups/", payload)
        ig_id = ig["id"]

    for jt_id in JT_IDS:
        try:
            api(
                "POST",
                f"/job_templates/{jt_id}/instance_groups/",
                {"id": 1, "disassociate": True},
            )
        except RuntimeError as exc:
            print(f"disassociate controlplane warn JT {jt_id}: {exc}")
        api("POST", f"/job_templates/{jt_id}/instance_groups/", {"id": ig_id})
        print(f"assigned JT {jt_id} -> {IG_NAME}")

    # Cancel stuck workflow/job if still pending for capacity
    _, jobs = api("GET", "/jobs/?status=pending&page_size=20")
    for job in jobs.get("results", []):
        if job.get("summary_fields", {}).get("job_template", {}).get("id") in JT_IDS:
            print(f"canceling pending job {job['id']}")
            try:
                api("POST", f"/jobs/{job['id']}/cancel/", {})
            except RuntimeError as exc:
                print(f"cancel warn: {exc}")

    print(json.dumps({"instance_group_id": ig_id, "credential_id": cred_id}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
