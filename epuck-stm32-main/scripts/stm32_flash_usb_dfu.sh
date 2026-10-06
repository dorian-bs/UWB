#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRESET="${1:-TxDebug}"
TARGET_ELF="${ROOT_DIR}/build/${PRESET}/EPuck_STM32.elf"
TARGET_BIN="${ROOT_DIR}/build/${PRESET}/EPuck_STM32.bin"
FLASH_ADDR="0x08000000"

source "${ROOT_DIR}/scripts/stm32_toolchain_env.sh"

if ! command -v dfu-util >/dev/null 2>&1; then
  echo "dfu-util not found. Install with: brew install dfu-util" >&2
  exit 1
fi

if [[ ! -f "${TARGET_ELF}" ]]; then
  echo "Missing ELF: ${TARGET_ELF}" >&2
  echo "Run build first: bash scripts/stm32_build.sh ${PRESET}" >&2
  exit 1
fi

if ! command -v arm-none-eabi-objcopy >/dev/null 2>&1; then
  echo "arm-none-eabi-objcopy not found in PATH." >&2
  exit 1
fi

arm-none-eabi-objcopy -O binary "${TARGET_ELF}" "${TARGET_BIN}"

echo "Waiting for STM32 DFU device (VID:PID 0483:df11)..."
if ! dfu-util -l | rg -qi "0483:df11|stm32.*bootloader"; then
  cat >&2 <<'EOF'
STM32 DFU device not detected.

Put board in system bootloader mode:
1) Hold BOOT button.
2) Press and release RESET while BOOT is held.
3) Release BOOT.
4) Re-run the command.
EOF
  exit 1
fi

# DfuSe format address argument: <address>:leave resets and exits DFU mode after flash.
dfu-util -a 0 -s "${FLASH_ADDR}:leave" -D "${TARGET_BIN}"

echo "Flashed ${TARGET_BIN} to ${FLASH_ADDR} via USB DFU."
