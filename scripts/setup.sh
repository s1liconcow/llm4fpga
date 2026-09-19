#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v podman >/dev/null; then
  echo 'Install Podman first (macOS: brew install podman).' >&2
  exit 1
fi
if [[ "$(uname -s)" == Darwin ]] && ! podman info >/dev/null 2>&1; then
  if [[ "$(podman machine list --format '{{.Name}}')" == '' ]]; then
    podman machine init --cpus 4 --memory 4096
  fi
  python3 - <<'PY'
import subprocess
subprocess.run(['podman', 'machine', 'start'], start_new_session=True, check=True)
PY
fi
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.12 .venv
fi
uv pip install --python .venv/bin/python -e '.[test]'
podman build -t xls-e2e-tools:local .
if [[ "${1:-}" == --xls ]]; then
  podman build --platform linux/amd64 -f Dockerfile.xls -t xls-e2e-xls:local .
fi
.venv/bin/python -m fpga_lab doctor
