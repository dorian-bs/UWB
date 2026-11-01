#!/usr/bin/env bash
# Flash TxDebug and RxDebug to two STM32 boards in USB DFU mode at the same time.
# DFU cannot tell the boards apart by role, so the first device (sorted by USB path) gets TX
# and the second gets RX. Set SWAP=1 to reverse that.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v dfu-util >/dev/null 2>&1; then
  echo "dfu-util not found. Install with: brew install dfu-util" >&2
  exit 1
fi

paths=()
while IFS= read -r path; do
  paths+=("${path}")
done < <(dfu-util -l | grep -i "0483:df11" | grep 'alt=0' | sed -n 's/.*path="\([^"]*\)".*/\1/p' | sort -u)

if [[ ${#paths[@]} -ne 2 ]]; then
  cat >&2 <<EOF
Expected exactly 2 STM32 DFU devices, found ${#paths[@]}.

Put each board in system bootloader mode:
1) Hold BOOT button.
2) Press and release RESET while BOOT is held.
3) Release BOOT.
Then re-run the command.
EOF
  exit 1
fi

tx_path="${paths[0]}"
rx_path="${paths[1]}"
if [[ "${SWAP:-0}" == "1" ]]; then
  tx_path="${paths[1]}"
  rx_path="${paths[0]}"
fi

echo "TxDebug -> USB path ${tx_path}"
echo "RxDebug -> USB path ${rx_path}"

DFU_PATH="${tx_path}" bash "${ROOT_DIR}/scripts/stm32_flash_usb_dfu.sh" TxDebug > >(sed 's/^/[TX] /') 2>&1 &
tx_pid=$!
DFU_PATH="${rx_path}" bash "${ROOT_DIR}/scripts/stm32_flash_usb_dfu.sh" RxDebug > >(sed 's/^/[RX] /') 2>&1 &
rx_pid=$!

status=0
wait "${tx_pid}" || status=1
wait "${rx_pid}" || status=1
exit "${status}"
