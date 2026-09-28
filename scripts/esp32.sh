#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IDF_DIR="${IDF_PATH:-${HOME}/esp/esp-idf}"

if [[ ! -f "${IDF_DIR}/export.sh" ]]; then
  echo "ESP-IDF not found at ${IDF_DIR}. Run: bash scripts/setup_macos.sh" >&2
  exit 1
fi

source "${IDF_DIR}/export.sh" >/dev/null
cd "${ROOT_DIR}/epuck-esp32-main"
exec python "${IDF_DIR}/tools/idf.py" "$@"
