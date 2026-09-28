#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
errors=0

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    printf "[ok] %-22s %s\n" "$1" "$(command -v "$1")"
  else
    printf "[missing] %s\n" "$1"
    errors=$((errors + 1))
  fi
}

check_command cmake
check_command ninja
check_command openocd

if source "${ROOT_DIR}/epuck-stm32-main/scripts/stm32_toolchain_env.sh" >/dev/null 2>&1; then
  printf "[ok] %-22s %s\n" "STM32 toolchain" "$(command -v arm-none-eabi-gcc)"
else
  echo "[missing] STM32 toolchain"
  errors=$((errors + 1))
fi

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]] && \
   "${ROOT_DIR}/.venv/bin/python" -c "import fastapi, serial, uvicorn, websockets" 2>/dev/null; then
  printf "[ok] %-22s %s\n" "Python host" "${ROOT_DIR}/.venv/bin/python"
else
  echo "[missing] Python host environment"
  errors=$((errors + 1))
fi

if idf_version="$("${ROOT_DIR}/scripts/esp32.sh" --version 2>/dev/null)"; then
  printf "[ok] %-22s %s\n" "ESP-IDF" "${idf_version}"
else
  echo "[missing] ESP-IDF"
  errors=$((errors + 1))
fi

if (( errors > 0 )); then
  echo "Setup is incomplete. Run: bash scripts/setup_macos.sh" >&2
  exit 1
fi

echo "All required tools are available."
