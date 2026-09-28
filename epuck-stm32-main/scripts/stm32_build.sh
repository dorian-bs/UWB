#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRESET="${1:-Debug}"

source "${ROOT_DIR}/scripts/stm32_toolchain_env.sh"

cd "${ROOT_DIR}"
cmake --preset "${PRESET}"
cmake --build --preset "${PRESET}" -j
