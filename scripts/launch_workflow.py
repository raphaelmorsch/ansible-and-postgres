#!/usr/bin/env python3
"""Launch an AAP workflow and wait until it finishes."""

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
API = f"{AAP_URL}/api/controller/v2"
AUTH = base64.b64encode(
    f"{os.environ.get('AAP_USERNAME', 'admin')}:{os.environ.get('AAP_PASSWORD', '083RpsIxThJl')}".encode()
).decode()
WF_NAME = os.environ.get(
    "WORKFLOW_NAME", "WF — Manutenção Preventiva PostgreSQL"
)


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
        raise RuntimeError(f"{method} {url} -> {exc.code}: {raw[:1200]}") from exc


def main():
    _, wfs = api("GET", f"/workflow_job_templates/?name={urllib.parse.quote(WF_NAME)}")
    if not wfs.get("count"):
        raise RuntimeError(f"workflow not found: {WF_NAME}")
    wf_id = wfs["results"][0]["id"]
    print(f"launching workflow {WF_NAME} (id={wf_id})")
    _, launch = api("POST", f"/workflow_job_templates/{wf_id}/launch/", {})
    job_id = launch["id"]
    print(f"workflow job id={job_id}")

    start = time.time()
    while time.time() - start < 1800:
        _, job = api("GET", f"/workflow_jobs/{job_id}/")
        status = job.get("status")
        print(f"status: {status}")
        if status in {"successful", "failed", "error", "canceled"}:
            print(json.dumps({"workflow_job_id": job_id, "status": status}, indent=2))
            if status != "successful":
                sys.exit(1)
            return
        time.sleep(10)
    raise TimeoutError(f"workflow job {job_id} did not finish in time")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
