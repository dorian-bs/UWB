#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IDF_VERSION="${IDF_VERSION:-v5.5.5}"
IDF_DIR="${IDF_PATH:-${HOME}/esp/esp-idf}"
ARM_RELEASE="14.3.rel1"
ARM_FOLDER="arm-gnu-toolchain-${ARM_RELEASE}-darwin-arm64-arm-none-eabi"
ARM_DIR="${HOME}/.local/${ARM_FOLDER}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This bootstrap currently supports macOS only." >&2
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2
  exit 1
fi

packages=()
command -v cmake >/dev/null 2>&1 || packages+=(cmake)
command -v ninja >/dev/null 2>&1 || packages+=(ninja)
command -v openocd >/dev/null 2>&1 || packages+=(open-ocd)
brew list --versions python@3.13 >/dev/null 2>&1 || packages+=(python@3.13)

if (( ${#packages[@]} > 0 )); then
  brew install "${packages[@]}"
fi

if [[ ! -x "${ARM_DIR}/bin/arm-none-eabi-gcc" ]]; then
  if [[ "$(uname -m)" != "arm64" ]]; then
    echo "Automatic Arm toolchain installation supports Apple Silicon only." >&2
    echo "Install STM32CubeIDE, then rerun this script." >&2
    exit 1
  fi

  archive="$(mktemp -t arm-gnu-toolchain).tar.xz"
  trap 'rm -f "${archive}"' EXIT
  echo "Installing Arm GNU Toolchain ${ARM_RELEASE}..."
  curl --fail --location --progress-bar \
    "https://developer.arm.com/-/media/Files/downloads/gnu/${ARM_RELEASE}/binrel/${ARM_FOLDER}.tar.xz" \
    --output "${archive}"
  mkdir -p "${HOME}/.local"
  tar -xf "${archive}" -C "${HOME}/.local"
fi

python_bin="$(brew --prefix python@3.13)/bin/python3.13"
if [[ ! -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  "${python_bin}" -m venv "${ROOT_DIR}/.venv"
fi
"${ROOT_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${ROOT_DIR}/.venv/bin/python" -m pip install -e "${ROOT_DIR}/host[dev]"

if [[ ! -d "${IDF_DIR}/.git" ]]; then
  mkdir -p "$(dirname "${IDF_DIR}")"
  git clone --recursive --depth 1 --shallow-submodules \
    --branch "${IDF_VERSION}" https://github.com/espressif/esp-idf.git "${IDF_DIR}"
fi

"${IDF_DIR}/install.sh" esp32s3
if [[ ! -f "${ROOT_DIR}/epuck-esp32-main/sdkconfig" ]] || \
   ! grep -q '^CONFIG_IDF_TARGET="esp32s3"$' "${ROOT_DIR}/epuck-esp32-main/sdkconfig"; then
  IDF_PATH="${IDF_DIR}" "${ROOT_DIR}/scripts/esp32.sh" set-target esp32s3
fi

echo
"${ROOT_DIR}/scripts/check_setup.sh"
echo
echo "Setup complete. Run 'make build' or 'make demo'."
