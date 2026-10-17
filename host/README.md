# UWB Lab Host

Small Python service for live viewing, dynamic geometry priors, rolling residual statistics, recording, and replay.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e './host[dev]'
```

Run without hardware:

```bash
uwb-lab --source demo
```

Open <http://127.0.0.1:8000>.

The demo source generates a sample every 200 ms, so the UI, priors, recording,
and replay can be tested before connecting the boards.

## Hardware sources

ESP32 TCP bridge:

```bash
uwb-lab --source tcp --tcp-host ESP32_IP
```

Direct STM32 USB serial:

```bash
uwb-lab --source serial --serial-port /dev/tty.usbmodemXXXX
```

### Live radio settings (two USB-connected boards)

Both boards need the updated STM32 firmware, with the initiator built as `TxDebug`
and the responder as `RxDebug`. Connect each board's USB CDC port to the host.
Keep the actual device paths in ignored `LAB_NOTES.md`; the variables below stand
for those local paths:

```bash
uwb-lab --source dual-serial --serial-port "$UWB_TX_USB" --responder-port "$UWB_RX_USB"
```

The **Radio / sampling** panel reads each board's identity and current settings.
It enables **Apply to both boards** only when both USB boards have been identified.
Controls are unavailable in demo, replay, TCP, and single-board serial modes.
These change physical radio settings, not a synthetic noise model:

- Requested ranging rate: 0.1-10 Hz, rounded to a 100-10000 ms poll period.
  Successful sample rate is measured separately from recent host arrivals.
  This limit is a command guard, **not a verified hardware throughput claim**.
- Channel: 5 or 9; preamble: 64, 128, 256, 512, or 1024 symbols.
- Data rate: 850 kbps or 6.8 Mbps.
- TX power: hexadecimal DW3000 register value, applied to both boards.
  It is **not dBm**; use only values approved for your boards and lab/regulatory setup.

See the [radio and sampling parameter reference](../docs/EXPERIMENTS_AND_VALIDATION.md#radio-and-sampling-parameter-reference)
for each setting's physical meaning, supported values, expected effects on bias/spread
and reception, dependent firmware settings, plot interpretation, and a comparison protocol.

Applying settings pauses both radios, configures and reads back both, then resumes
the responder before the initiator. A missing acknowledgement or mismatch triggers
rollback. If rollback cannot be confirmed, reachable boards are left paused, with
individual state/errors shown; disconnected board state is unknown, not assumed safe.
Reachable boards are restored independently where pause/readback can be confirmed;
they do not resume if the complete pair cannot be confirmed. Control acknowledgements
are independent of slow graph clients; discarded USB input lines are counted in the panel.
After restoring connectivity, inspect the states and apply a compatible configuration
to recover. **Load current TX settings** fills the form without changing either board.
Settings are volatile and return to firmware defaults after a board restart.
The UI never automatically applies saved hardware settings.

Graphs keep earlier samples. Recordings include `radio.ndjson` with settings snapshots
and apply/rollback outcomes; range records carry `radio_revision` so earlier measurements
are not relabelled. A board's revision is local to its boot, not a globally unique ID.
Record restarts as separate sessions until boot IDs are available.

Host tests use isolated pseudo-terminals and native C tests, not physical boards.
Build/flash and on-hardware rate/RF validation must still be performed explicitly.

Recorded session:

```bash
uwb-lab --source replay --replay-file data/sessions/SESSION/telemetry.ndjson
```

Recordings are written under `data/sessions/` and are intentionally ignored by Git.

## Firmware setup

Build the two STM32 roles from `epuck-stm32-main/`:

```bash
bash scripts/stm32_build.sh TxDebug
bash scripts/stm32_build.sh RxDebug
```

Flash the corresponding connected board with `scripts/stm32_flash.sh TxDebug`
or `scripts/stm32_flash.sh RxDebug`. The TX board calculates and emits the range.

For the ESP32 bridge, install/export ESP-IDF first, then run from
`epuck-esp32-main/`:

```bash
idf.py menuconfig
idf.py build
idf.py flash monitor
```

Set the SSID, password, and TCP port under **UWB bridge configuration** in
`menuconfig`. If `command -v idf.py` prints nothing, ESP-IDF has not been loaded
in that shell; source the `export.sh` file from your ESP-IDF installation.

## Tests

```bash
python -m pytest host/tests
```