 # UWB Ranging Project

Semester-project setup for DW3000 ranging, an STM32/ESP32 link, and a small Python live dashboard.

Working on this with an AI coding agent? Point it at [CLAUDE.md](CLAUDE.md) — it is the
orientation doc that explains the layout, the telemetry contract, and what to run without hardware.

Run `make help` to see every command, grouped by whether it needs hardware.

## New Mac setup

Requirements: macOS on Apple Silicon and [Homebrew](https://brew.sh).

Clone and set up the project:

```bash
git clone https://github.com/dorian-bs/UWB.git
cd UWB
bash scripts/setup_macos.sh
make build
make test
```

The setup is safe to rerun. It installs:

- CMake, Ninja, OpenOCD, dfu-util, Python 3.13, and Node.js (dashboard tests only)
  through Homebrew when missing.
- Arm GNU Toolchain 14.3.rel1 under `~/.local/`.
- ESP-IDF v5.5.5 and ESP32-S3 tools under `~/esp/` and `~/.espressif/`.
- Host Python dependencies in the repository `.venv/`.

Use `make check` at any time to diagnose an incomplete setup.

## Try the dashboard

```bash
make demo
```

Open <http://127.0.0.1:8000>. This uses generated data and requires no hardware.

To see the dashboard against a recorded-looking session, including a simulated NLOS
obstruction, use `make replay`.

### Explore the data

Each plot has its own checkboxes for raw/corrected/expected distance (left axis) and
residual, rolling bias and spread (right axis). Enable **Show Plot 2** for a second plot
with all six traces available independently; it initially selects residual only.
Open **Plot 1 axis limits** or **Plot 2 axis limits** for that plot's auto/manual scales.
Selections and limits are remembered separately, including when Plot 2 is hidden.
The shared toolbar sets the last N samples, plot height, and lines/points for both plots.
**Hide experiment controls** gives the plots more room.
**Radio / sampling** and **Geometry prior** expand independently when you click their
headings. Both start collapsed on page load. Collapsing them preserves entered values
and does not stop acquisition or recording; the Recording section remains visible.

**Pause view** freezes charts and measurement readouts while acquisition, recording and
health counters continue. Resume returns to the latest samples. Display settings are
remembered locally; **Reset display** restores defaults without changing your prior or
recording. The display window does not change the host's 20-residual rolling calculations.
Up to 2000 samples accumulate in the browser; reconnecting starts from the host's
up-to-300-sample snapshot. Missing prior-derived values are not plotted as zero.

### Change the physical radio while plotting

With updated STM32 firmware on both boards, connect both USB CDC ports and run:

```bash
bash scripts/run_dashboard.sh --source dual-serial --serial-port "$UWB_TX_USB" --responder-port "$UWB_RX_USB"
```

The variables stand for your local initiator/responder device paths; keep those in
ignored `LAB_NOTES.md`. The **Radio / sampling** panel changes ranging cadence,
channel, preamble, data rate, and raw TX power on both boards, with acknowledgements
and rollback if a change fails. Existing graph history is retained. Requested
cadence and observed successful-sample rate are shown separately.
Demo, replay, and single-board connections cannot change physical radio settings.
See [dual-USB instructions and limitations](host/README.md#live-radio-settings-two-usb-connected-boards).

## Change host code

```bash
make verify   # ruff lint/format check + all host tests
make fmt      # auto-format and auto-fix
```

`make verify` needs no boards. Radio tests compile small native C harnesses and use
isolated virtual serial ports; a native C compiler is required. Run it before committing host changes.
The dashboard logic tests also require Node.js 18+ (`brew install node` if missing);
Node is only used for testing, not running the dashboard.

## Configure and flash hardware

Configure the ESP32 network without storing credentials in Git:

```bash
make esp-config
make esp-flash
```

Build both STM32 images with `make build`. Then connect the appropriate board and run:

```bash
make stm-tx-flash
make stm-rx-flash
```
if using a ST-link, or:
```bash
make stm-tx-dfu-flash
make stm-rx-dfu-flash
```
if using USB-C.

Only one STM32 should be connected when running each flash command. The TX board computes and publishes range measurements.

Start the dashboard with the ESP32 IP address:

```bash
bash scripts/run_dashboard.sh --source tcp --tcp-host ESP32_IP
```

## Common runtime commands

Use these commands during bring-up and debugging.

Addresses and serial paths differ per machine and per lab session, so they are not recorded here.
Keep yours in `LAB_NOTES.md` — copy [the template](docs/LAB_NOTES.example.md) to start one:

```bash
cp docs/LAB_NOTES.example.md LAB_NOTES.md
```

The resulting file is gitignored.

### List connected USB serial ports

```bash
ls /dev/tty.usbmodem*
ls /dev/cu.usbmodem*
```

- Shows currently connected USB CDC devices (STM32 USB CDC, ESP32 USB-Serial/JTAG, etc.).
- On macOS, `/dev/cu.*` is typically preferred for active terminal/monitor connections.

### Attach ESP-IDF monitor without reflashing

```bash
bash scripts/esp32.sh -p /dev/cu.usbmodemXXXX monitor
```

- Opens the serial monitor for an already flashed ESP32.
- Useful to read logs such as `Got IP: ...` without writing new firmware.

If multiple ESP-class devices are connected, always pass `-p` explicitly.

Optional convenience variable:

```bash
export ESPPORT=/dev/cu.usbmodemXXXX
bash scripts/esp32.sh monitor
```

- Uses `ESPPORT` as the default monitor/flash port in ESP-IDF tooling.
- The monitor prints `Got IP: ...` at boot. That is where the bridge address comes from; note it
  in `LAB_NOTES.md` rather than here, since DHCP reassigns it.

### Check TCP bridge reachability from your Mac

```bash
nc 192.168.x.xxx 3333
```

- Opens a raw TCP connection to the ESP32 bridge.
- Expected first line on success:

```text
ESP32 UART->TCP bridge connected
```

- Data from STM32 UART should appear in this terminal while connected.

### Run dashboard from serial source (direct STM32 USB)

```bash
bash scripts/run_dashboard.sh --source serial --serial-port /dev/tty.usbmodemXXXX
```

- Reads telemetry directly from STM32 USB CDC instead of ESP32 TCP.

### Run dashboard from ESP32 TCP source

```bash
bash scripts/run_dashboard.sh --source tcp --tcp-host 192.168.x.xxx --tcp-port 3333
```

- Reads telemetry through the ESP32 UART-to-TCP bridge.

### Useful monitor controls

- `Ctrl+]`: quit `idf.py monitor`.
- `Ctrl+C`: stop `nc` or most foreground terminal commands.

For the documentation map, see [docs/README.md](docs/README.md); for progress, limitations,
and the phased roadmap, see [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).