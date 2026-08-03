#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=demo-git
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export COPYFILE_DISABLE=1

echo "Preparing playbooks browser bundle from $ROOT"
WORKDIR="$TMP/www"
mkdir -p "$WORKDIR/playbooks" "$WORKDIR/inventory" "$WORKDIR/collections"

cp -R "$ROOT/playbooks/." "$WORKDIR/playbooks/"
cp -R "$ROOT/inventory/." "$WORKDIR/inventory/"
cp -R "$ROOT/collections/." "$WORKDIR/collections/"
cp "$ROOT/ansible.cfg" "$WORKDIR/"
[[ -f "$ROOT/README.md" ]] && cp "$ROOT/README.md" "$WORKDIR/"

WORKDIR="$WORKDIR" python3 - <<'PY'
from pathlib import Path
import os

workdir = Path(os.environ["WORKDIR"])
playbooks = sorted((workdir / "playbooks").glob("*.yml"))
items = "\n".join(
    f'      <li><a href="playbooks/{p.name}"><code>{p.name}</code></a></li>'
    for p in playbooks
)
html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>AAP Demo — Playbooks</title>
  <style>
    :root {{ --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --accent:#38bdf8; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin:0; font-family:"Segoe UI", system-ui, sans-serif;
      background: linear-gradient(160deg,#0b1224,#172554 55%,#0f172a);
      color: var(--text); min-height:100vh;
    }}
    main {{ max-width: 900px; margin: 0 auto; padding: 2rem; }}
    h1 {{ margin:0 0 .4rem; font-size:1.6rem; }}
    p {{ color: var(--muted); }}
    .card {{
      background: var(--card); border:1px solid #334155; border-radius:12px;
      padding:1.25rem 1.5rem; margin-top:1.25rem;
    }}
    ul {{ margin:.4rem 0 0; padding-left:1.2rem; line-height:1.9; }}
    a {{ color: var(--accent); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    code {{ font-size:.95rem; }}
    .meta {{ font-size:.85rem; color: var(--muted); margin-top:1rem; }}
  </style>
</head>
<body>
  <main>
    <h1>Playbooks — Manutenção Preventiva PostgreSQL</h1>
    <p>YAMLs usados pelo workflow do Ansible Automation Platform. Clique para abrir no browser.</p>
    <div class="card">
      <strong>Job Templates</strong>
      <ul>
{items}
      </ul>
    </div>
    <div class="card">
      <strong>Outros arquivos</strong>
      <ul>
        <li><a href="inventory/hosts.yml"><code>inventory/hosts.yml</code></a></li>
        <li><a href="inventory/group_vars/all.yml"><code>inventory/group_vars/all.yml</code></a></li>
        <li><a href="collections/requirements.yml"><code>collections/requirements.yml</code></a></li>
        <li><a href="ansible.cfg"><code>ansible.cfg</code></a></li>
        <li><a href="README.md"><code>README.md</code></a></li>
        <li><a href="playbooks/"><code>playbooks/</code></a> (listagem)</li>
      </ul>
    </div>
    <p class="meta">Projeto demo-git · arquivos servidos como texto</p>
  </main>
</body>
</html>
"""
(workdir / "index.html").write_text(html)
print(f"index with {len(playbooks)} playbooks")
PY

tar -C "$WORKDIR" -czf "$TMP/www.tgz" .
python3 - <<PY
import base64, pathlib
data = pathlib.Path("$TMP/www.tgz").read_bytes()
pathlib.Path("$TMP/www.b64").write_text(base64.b64encode(data).decode())
print(f"www_bytes={len(data)}")
PY

oc create namespace "$NS" --dry-run=client -o yaml | oc apply -f -
oc -n "$NS" create configmap demo-playbooks-www \
  --from-file=www.b64="$TMP/www.b64" \
  --dry-run=client -o yaml | oc apply -f -

oc apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: demo-playbooks-ui-entrypoint
  namespace: $NS
data:
  entrypoint.sh: |
    #!/bin/bash
    set -euo pipefail
    ROOT=/var/www/html
    mkdir -p /tmp/extract "\$ROOT"
    base64 -d /bundle/www.b64 > /tmp/www.tgz
    # Extract to temp dir to avoid emptyDir utime/chmod failures on "."
    tar -C /tmp/extract -xzf /tmp/www.tgz --warning=no-unknown-keyword || true
    find /tmp/extract -name '._*' -delete || true
    cp -r /tmp/extract/. "\$ROOT"/
    cat > "\$ROOT/.htaccess" <<'HTA'
    AddType text/plain .yml .yaml .md .cfg
    Options +Indexes
    HTA
    chmod -R a+rX "\$ROOT" || true
    echo "Playbooks UI ready at \$ROOT"
    ls -la "\$ROOT"
    exec /usr/bin/run-httpd
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-playbooks-ui
  namespace: $NS
  labels:
    app: demo-playbooks-ui
spec:
  replicas: 1
  selector:
    matchLabels:
      app: demo-playbooks-ui
  template:
    metadata:
      labels:
        app: demo-playbooks-ui
    spec:
      containers:
        - name: httpd
          image: image-registry.openshift-image-registry.svc:5000/openshift/httpd:2.4-ubi9
          command: ["/bin/bash", "/entrypoint/entrypoint.sh"]
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: bundle
              mountPath: /bundle
              readOnly: true
            - name: entrypoint
              mountPath: /entrypoint
              readOnly: true
            - name: html
              mountPath: /var/www/html
          readinessProbe:
            httpGet:
              path: /
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 20
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 300m
              memory: 256Mi
      volumes:
        - name: bundle
          configMap:
            name: demo-playbooks-www
        - name: entrypoint
          configMap:
            name: demo-playbooks-ui-entrypoint
            defaultMode: 0755
        - name: html
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: demo-playbooks-ui
  namespace: $NS
spec:
  selector:
    app: demo-playbooks-ui
  ports:
    - name: http
      port: 80
      targetPort: 8080
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: demo-playbooks-ui
  namespace: $NS
spec:
  to:
    kind: Service
    name: demo-playbooks-ui
  port:
    targetPort: http
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
EOF

oc rollout restart deployment/demo-playbooks-ui -n "$NS" >/dev/null 2>&1 || true
oc rollout status deployment/demo-playbooks-ui -n "$NS" --timeout=240s
HOST="$(oc get route demo-playbooks-ui -n "$NS" -o jsonpath='{.spec.host}')"
echo
echo "Playbooks UI: https://${HOST}/"
echo "Exemplo:     https://${HOST}/playbooks/jt01_health_check.yml"
