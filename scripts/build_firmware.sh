#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}/epuck-stm32-main"
bash scripts/stm32_build.sh TxDebug
bash scripts/stm32_build.sh RxDebug

"${ROOT_DIR}/scripts/esp32.sh" build
