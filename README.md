# UWB Ranging Project

A university semester project for measuring DW3000 ranging noise. Two STM32 nodes
measure distance; a small Python dashboard plots, records, replays, and analyses it.
An ESP32 can bridge the telemetry over Wi-Fi.

The project deliberately uses plain Python, HTML, CSS, and JavaScript: no frontend
build step, database server, or deployment platform.

## Start without hardware

Use Python **3.11 or newer**. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './host[dev]'
make demo
```

Open <http://127.0.0.1:8000>. Stop with `Ctrl+C`.
Use `make replay` instead to view the committed synthetic session with an NLOS-like
obstruction. Neither command needs firmware tools or boards.

The dashboard supports:

- Raw/corrected distance, a geometry prior, residuals, and rolling bias/spread.
- Recording and replay, with raw measurements kept unchanged.
- Repeated timed runs and basic noise analysis: statistics, histogram with a
  selectable distribution fit (Gaussian, mixture, log-normal, ...), and a spectrum over all runs.
- A suggestion-only antenna-delay calibration assistant.
- Physical radio controls when both boards are connected over USB.

See the [host guide](host/README.md) for the controls, data files, and limitations.

## Change and check code

```bash
make verify   # Python lint/format checks, host/API tests, JS tests, native C harnesses
make fmt      # Format and auto-fix Python
make help     # All available commands
```

Full verification also needs **Node.js 18+** and a native **C compiler**.
Node is for tests only; it is not needed to run the dashboard.
On macOS, install Node with `brew install node` and the compiler with
`xcode-select --install` if missing. The native firmware tests do not need the Arm
toolchain; they are skipped if no native compiler is available.

| Where | What to edit |
| --- | --- |
| [host/uwb_lab/](host/uwb_lab/) | Acquisition, priors, recording, experiments, analysis, API |
| [host/uwb_lab/static/](host/uwb_lab/static/) | Plain browser UI |
| [host/tests/](host/tests/) | Hardware-free regression tests |
| [qorvo_port.c](epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c) | STM32 radio/ranging logic |
| [ESP32 main.c](epuck-esp32-main/main/main.c) | UART/TCP transport |

Keep changes small and easy to explain. Preserve raw data, test with synthetic or
recorded lines, and do not add infrastructure for hypothetical future needs.
AI coding agents should start with [CLAUDE.md](CLAUDE.md).

## Hardware setup on a new Mac

The full bootstrap supports macOS on Apple Silicon with [Homebrew](https://brew.sh):

```bash
make setup
make check
make build
```

`make setup` installs missing host tools/dependencies, Arm GNU Toolchain 14.3.rel1,
and ESP-IDF v5.5.5. It is safe to rerun, but is unnecessary for host-only work if
you followed the quick start above. `make check` reports host and firmware tools
separately; missing firmware tools do not prevent demo/replay.

### Configure and flash

Set Wi-Fi credentials in the ignored ESP32 configuration, never in source:

```bash
make esp-config
make esp-flash
```

After building the STM32 images, connect **only the board being flashed**:

| Board | ST-Link | USB DFU bootloader |
| --- | --- | --- |
| Initiator (TX) | `make stm-tx-flash` | `make stm-tx-dfu-flash` |
| Responder (RX) | `make stm-rx-flash` | `make stm-rx-dfu-flash` |

Only the initiator publishes range measurements. Firmware builds and flashing
are separate from `make verify` and require the appropriate tools/boards.

### Connect the dashboard

Addresses and serial paths are local to your lab. Copy the ignored notes template:

```bash
cp docs/LAB_NOTES.example.md LAB_NOTES.md
```

List USB ports with `ls /dev/cu.usbmodem*` on macOS, then set local shell variables
for the connections you use:

```bash
# Direct STM32 USB
bash scripts/run_dashboard.sh --source serial --serial-port "$UWB_TX_USB"

# Two USB-connected boards, with radio controls
bash scripts/run_dashboard.sh --source dual-serial \
  --serial-port "$UWB_TX_USB" --responder-port "$UWB_RX_USB"

# ESP32 bridge; use the address printed by its "Got IP" boot log
bash scripts/run_dashboard.sh --source tcp --tcp-host "$UWB_BRIDGE_HOST"
```

Hardware modes require explicit addresses/ports; there are no guessed device
defaults. For an ESP32 monitor without flashing:
`bash scripts/esp32.sh -p "$ESPPORT" monitor`. Quit that monitor with `Ctrl+]`.
The bridge accepts one TCP client on port 3333 by default and is intended only
for a trusted lab network.

## Documentation

- [Host guide](host/README.md): dashboard workflows and recording layout.
- [Documentation map](docs/README.md): architecture and scientific references.
- [Current status](docs/IMPLEMENTATION_STATUS.md): working features, limits, next steps.
