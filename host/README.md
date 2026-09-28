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