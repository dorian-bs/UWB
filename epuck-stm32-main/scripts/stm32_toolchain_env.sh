#!/usr/bin/env bash

# Resolve STM32 GNU Arm toolchain location.
# Priority:
# 1) STM32_TOOLCHAIN_BIN environment variable
# 2) Toolchain installed by scripts/setup_macos.sh
# 3) Common STM32CubeIDE plugin path
# 4) Newest matching STM32CubeIDE plugin path

if [[ -n "${STM32_TOOLCHAIN_BIN:-}" && -x "${STM32_TOOLCHAIN_BIN}/arm-none-eabi-gcc" ]]; then
  TOOLCHAIN_BIN="${STM32_TOOLCHAIN_BIN}"
elif [[ -x "${HOME}/.local/arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi/bin/arm-none-eabi-gcc" ]]; then
  TOOLCHAIN_BIN="${HOME}/.local/arm-gnu-toolchain-14.3.rel1-darwin-arm64-arm-none-eabi/bin"
else
  DEFAULT_BIN="/Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.macosaarch64_1.0.0.202602081740/tools/bin"

  if [[ -x "${DEFAULT_BIN}/arm-none-eabi-gcc" ]]; then
    TOOLCHAIN_BIN="${DEFAULT_BIN}"
  else
    TOOLCHAIN_BIN=""
    while IFS= read -r candidate; do
      if [[ -x "${candidate}/arm-none-eabi-gcc" ]]; then
        TOOLCHAIN_BIN="${candidate}"
        break
      fi
    done < <(ls -d /Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin 2>/dev/null | sort -r)
  fi
fi

if [[ -z "${TOOLCHAIN_BIN:-}" ]]; then
  cat <<'EOF' >&2
STM32 toolchain not found.
Run the repository setup script, install STM32CubeIDE, or set STM32_TOOLCHAIN_BIN
to a complete Arm GNU Toolchain folder containing arm-none-eabi-gcc and Newlib.
Example:
  export STM32_TOOLCHAIN_BIN="/Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/.../tools/bin"
EOF
  exit 1
fi

export PATH="${TOOLCHAIN_BIN}:$PATH"

echo "Using STM32 toolchain: ${TOOLCHAIN_BIN}"
