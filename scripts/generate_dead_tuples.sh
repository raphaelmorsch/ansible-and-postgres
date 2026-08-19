#!/usr/bin/env bash
# Create/replace an OpenShift Job that generates PostgreSQL dead tuples for the demo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${POSTGRES_NAMESPACE:-databases}"
JOB_NAME="${JOB_NAME:-generate-dead-tuples}"
SQL_FILE="$ROOT/sql/generate_dead_tuples.sql"

if [[ ! -f "$SQL_FILE" ]]; then
  echo "SQL file not found: $SQL_FILE" >&2
  exit 1
fi

echo "==> Preparing Job ${JOB_NAME} in namespace ${NS}"

# Encode SQL into a ConfigMap
oc -n "$NS" create configmap generate-dead-tuples-sql \
  --from-file=generate_dead_tuples.sql="$SQL_FILE" \
  --dry-run=client -o yaml | oc apply -f -

# Remove previous job (Jobs are immutable)
oc -n "$NS" delete job "$JOB_NAME" --ignore-not-found=true --wait=true

# Detect postgresql image currently used by the DeploymentConfig
PG_IMAGE="$(oc -n "$NS" get dc postgresql -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
if [[ -z "$PG_IMAGE" ]]; then
  PG_IMAGE="image-registry.openshift-image-registry.svc:5000/openshift/postgresql:10-el8"
fi
echo "Using image: $PG_IMAGE"

oc apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: ${NS}
  labels:
    app: generate-dead-tuples
    app.kubernetes.io/part-of: aap-pg-maintenance
spec:
  ttlSecondsAfterFinished: 1800
  backoffLimit: 1
  template:
    metadata:
      labels:
        app: generate-dead-tuples
    spec:
      restartPolicy: Never
      containers:
        - name: generate-dead-tuples
          image: ${PG_IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: PGHOST
              value: postgresql
            - name: PGPORT
              value: "5432"
            - name: PGUSER
              valueFrom:
                secretKeyRef:
                  name: postgresql
                  key: database-user
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgresql
                  key: database-password
            - name: PGDATABASE
              valueFrom:
                secretKeyRef:
                  name: postgresql
                  key: database-name
          command:
            - /bin/bash
            - -ec
            - |
              echo "Generating dead tuples on \${PGHOST}/\${PGDATABASE} as \${PGUSER}"
              psql -v ON_ERROR_STOP=1 -f /sql/generate_dead_tuples.sql
              echo "Done."
          volumeMounts:
            - name: sql
              mountPath: /sql
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: "1"
              memory: 512Mi
      volumes:
        - name: sql
          configMap:
            name: generate-dead-tuples-sql
EOF

echo "==> Waiting for Job to complete..."
oc -n "$NS" wait --for=condition=complete "job/${JOB_NAME}" --timeout=300s

POD="$(oc -n "$NS" get pods -l job-name=${JOB_NAME} -o jsonpath='{.items[0].metadata.name}')"
echo "==> Job logs (${POD}):"
oc -n "$NS" logs "$POD"

echo
echo "Dead tuples ready. Launch the AAP workflow:"
echo "  WF — Manutenção Preventiva PostgreSQL"
echo
echo "Re-run anytime with:"
echo "  ./scripts/generate_dead_tuples.sh"
