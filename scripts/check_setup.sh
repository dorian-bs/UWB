#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host_errors=0
firmware_errors=0

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    printf "[ok] %-22s %s\n" "$1" "$(command -v "$1")"
  else
    printf "[missing] %s\n" "$1"
    firmware_errors=$((firmware_errors + 1))
  fi
}

echo "Host tools (needed for: make verify, make test, make demo, make replay)"

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]] && \
   "${ROOT_DIR}/.venv/bin/python" -c "import fastapi, serial, uvicorn, websockets, pytest, ruff" 2>/dev/null; then
  printf "[ok] %-22s %s\n" "Python host" "${ROOT_DIR}/.venv/bin/python"
else
  echo "[missing] Python host environment"
  host_errors=$((host_errors + 1))
fi

echo
echo "Firmware toolchains (needed for: make build, make esp-*, make stm-*)"

check_command cmake
check_command ninja
check_command openocd
check_command dfu-util

if source "${ROOT_DIR}/epuck-stm32-main/scripts/stm32_toolchain_env.sh" >/dev/null 2>&1; then
  printf "[ok] %-22s %s\n" "STM32 toolchain" "$(command -v arm-none-eabi-gcc)"
else
  echo "[missing] STM32 toolchain"
  firmware_errors=$((firmware_errors + 1))
fi

if idf_version="$("${ROOT_DIR}/scripts/esp32.sh" --version 2>/dev/null)"; then
  printf "[ok] %-22s %s\n" "ESP-IDF" "${idf_version}"
else
  echo "[missing] ESP-IDF"
  firmware_errors=$((firmware_errors + 1))
fi

echo
if (( host_errors > 0 )); then
  echo "Host environment is incomplete; host work cannot run. Fix: bash scripts/setup_macos.sh" >&2
elif (( firmware_errors > 0 )); then
  echo "Host work is ready. Firmware toolchains are incomplete; only firmware builds and flashing are blocked." >&2
fi

if (( host_errors + firmware_errors > 0 )); then
  exit 1
fi

echo "All required tools are available."
