#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "${ROOT_DIR}/.venv/bin/uwb-lab" ]]; then
  echo "Host environment not found. Run: bash scripts/setup_macos.sh" >&2
  exit 1
fi

cd "${ROOT_DIR}"
exec "${ROOT_DIR}/.venv/bin/uwb-lab" "$@"
