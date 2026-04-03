#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

if [[ ! -f .env ]]; then
  echo "missing .env in ${REPO_ROOT}" >&2
  exit 1
fi

set -a
source .env
set +a

if [[ -z "${CAPTURE_DEVICE_NAME:-}" ]]; then
  echo "missing CAPTURE_DEVICE_NAME in ${REPO_ROOT}/.env" >&2
  exit 1
fi

exec uv run ambient-memory agent run --device "${CAPTURE_DEVICE_NAME}"
