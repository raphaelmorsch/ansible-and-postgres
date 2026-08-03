#!/usr/bin/env bash
set -euo pipefail

# Thin wrapper kept for compatibility — prefers the OpenShift Job.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/generate_dead_tuples.sh"
