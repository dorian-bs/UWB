#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRESET="${1:-Debug}"
TARGET_ELF="${ROOT_DIR}/build/${PRESET}/EPuck_STM32.elf"

source "${ROOT_DIR}/scripts/stm32_toolchain_env.sh"

if ! command -v openocd >/dev/null 2>&1; then
  echo "openocd not found. Install with: brew install open-ocd" >&2
  exit 1
fi

if [[ ! -f "${TARGET_ELF}" ]]; then
  echo "Missing ELF: ${TARGET_ELF}" >&2
  echo "Run build first: bash scripts/stm32_build.sh ${PRESET}" >&2
  exit 1
fi

cd "${ROOT_DIR}"
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "program ${TARGET_ELF} verify reset exit"
