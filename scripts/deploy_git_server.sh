#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=demo-git
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export COPYFILE_DISABLE=1

echo "Preparing bare git repo from $ROOT"
WORKDIR="$TMP/src"
mkdir -p "$WORKDIR"
cp -R "$ROOT/playbooks" "$WORKDIR/"
cp -R "$ROOT/inventory" "$WORKDIR/"
cp -R "$ROOT/collections" "$WORKDIR/"
cp "$ROOT/ansible.cfg" "$WORKDIR/"
[[ -f "$ROOT/README.md" ]] && cp "$ROOT/README.md" "$WORKDIR/"

cd "$WORKDIR"
git init -b main >/dev/null
git config user.email "demo@example.com"
git config user.name "AAP Demo"
git add .
git -c commit.gpgsign=false commit -m "AAP PostgreSQL preventive maintenance demo" >/dev/null

git clone --bare "$WORKDIR" "$TMP/ansible-and-postgres.git" >/dev/null
git --git-dir="$TMP/ansible-and-postgres.git" update-server-info
touch "$TMP/ansible-and-postgres.git/git-daemon-export-ok"

# Pack bare repo for ConfigMap (no git needed in cluster)
tar -C "$TMP" -czf "$TMP/repo.tgz" ansible-and-postgres.git
python3 - <<PY
import base64, pathlib
data = pathlib.Path("$TMP/repo.tgz").read_bytes()
pathlib.Path("$TMP/repo.b64").write_text(base64.b64encode(data).decode())
print(f"repo_bytes={len(data)}")
PY

oc create namespace "$NS" --dry-run=client -o yaml | oc apply -f -

oc -n "$NS" create configmap demo-git-repo \
  --from-file=repo.b64="$TMP/repo.b64" \
  --dry-run=client -o yaml | oc apply -f -

oc apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: demo-git-entrypoint
  namespace: $NS
data:
  entrypoint.sh: |
    #!/bin/bash
    set -euo pipefail
    ROOT=/var/www/html
    REPO=\$ROOT/ansible-and-postgres.git
    mkdir -p "\$ROOT"
    rm -rf "\$REPO"
    base64 -d /bundle/repo.b64 > /tmp/repo.tgz
    tar -C "\$ROOT" -xzf /tmp/repo.tgz
    chmod -R a+rX "\$REPO"
    echo "Repo ready at \$REPO"
    exec /usr/bin/run-httpd
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-git
  namespace: $NS
spec:
  replicas: 1
  selector:
    matchLabels:
      app: demo-git
  template:
    metadata:
      labels:
        app: demo-git
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
              path: /ansible-and-postgres.git/HEAD
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /ansible-and-postgres.git/HEAD
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
            name: demo-git-repo
        - name: entrypoint
          configMap:
            name: demo-git-entrypoint
            defaultMode: 0755
        - name: html
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: demo-git
  namespace: $NS
spec:
  selector:
    app: demo-git
  ports:
    - name: http
      port: 80
      targetPort: 8080
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: demo-git
  namespace: $NS
spec:
  to:
    kind: Service
    name: demo-git
  port:
    targetPort: http
EOF

oc delete configmap -n "$NS" demo-git-src --ignore-not-found >/dev/null 2>&1 || true
oc rollout restart deployment/demo-git -n "$NS" >/dev/null
oc rollout status deployment/demo-git -n "$NS" --timeout=240s
echo "Git server ready: http://demo-git.${NS}.svc.cluster.local/ansible-and-postgres.git"
curl -sS "http://$(oc get route demo-git -n $NS -o jsonpath='{.spec.host}')/ansible-and-postgres.git/HEAD"
echo
