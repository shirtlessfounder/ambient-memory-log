#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

if [[ ! -f .env.teammate ]]; then
  echo "missing .env.teammate in ${REPO_ROOT}" >&2
  exit 1
fi

if [[ ! -f .env.room-mic ]]; then
  echo "missing .env.room-mic in ${REPO_ROOT}" >&2
  exit 1
fi

exec uv run ambient-memory start-dual-capture
