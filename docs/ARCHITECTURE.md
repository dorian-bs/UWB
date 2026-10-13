# Architecture, Codebase, and Data Contract

**Current:** firmware and host behavior documented on 2026-09-28.
**Planned:** richer protocol, storage, and experiment-console requirements below.
See [implementation status and roadmap](IMPLEMENTATION_STATUS.md) for delivery order.

## Current system and ownership

```text
DW3000 --SPI/IRQ--> STM32F405 initiator <--DS-TWR--> STM32F405 responder + DW3000
                         | USART3/115200
                         v
                    ESP32-S3 --TCP:3333--> Python host --> browser UI
                         STM32 --USB CDC-->     |         |
                                               +--> NDJSON sessions --> replay
```

- **STM32:** deterministic radio timing, ranging, hardware diagnostics, monotonic sample identity.
- **ESP32:** optional transport and network provisioning; no scientific correction logic.
- **Host:** recording, priors, derived values, visualization, annotations, models, reproducibility.

| Path | Responsibility |
| --- | --- |
| [STM32 main.c](../epuck-stm32-main/Core/Src/main.c) | CubeMX startup; project edits belong in `USER CODE` regions |
| [qorvo_port.c](../epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c) | Radio configuration, DS-TWR state machine, timestamp exchange, range maths, calibration, telemetry |
| [qorvo_platform.h](../epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_platform.h) | Public link/platform API; supporting files implement SPI, IRQ, sleep callbacks |
| [dwm3000_port.c](../epuck-stm32-main/Core/Src/dwm3000_port.c) | Compiled earlier/basic SPI device-ID test; main uses the Qorvo API instead |
| [usbd_cdc_if.c](../epuck-stm32-main/USB_DEVICE/App/usbd_cdc_if.c) | Generated USB virtual COM interface |
| [STM32 CMakeLists.txt](../epuck-stm32-main/CMakeLists.txt) | CubeMX sources, platform layer, vendor driver; floating-point `printf` enabled |
| [ESP32 main.c](../epuck-esp32-main/main/main.c) | ESP-IDF application with two FreeRTOS bridge tasks |
| [host/uwb_lab/](../host/uwb_lab/) / [host/tests/](../host/tests/) | FastAPI acquisition/analysis/recording/replay and hardware-free tests |
| [scripts/](../scripts/) / [data/](../data/) | Thin setup/run/build/flash wrappers; ignored local sessions under `data/sessions/` |
| [uwb.code-workspace](../uwb.code-workspace) | VS Code workspace definition |

### STM32

- Initializes GPIO, ADC1, I2C1, SPI1, USART1, USART3, USB CDC, then the Qorvo link.
  The main loop calls `qorvo_link_process()` and toggles a 1 Hz status LED.
- `TxDebug`/`RxDebug` presets select board identity and initiator/responder role.
- SPI1 starts with a conservative DW3000 initialization prescaler. An interrupt-driven
  `POLL`/`ANSWER`/`FINAL`/`REPORT` exchange runs every 1000 ms; the initiator computes raw
  and corrected range from six timestamps after a matching report.
- Compile-time antenna-delay calibration collects statistics at a hard-coded known distance.
  The disabled range-bias hook currently returns unmodified range.
- `_write()` redirects `printf` to USB CDC; Qorvo logging also uses USART3 at 115200 baud.
  Readable `ds_twr.distance`/`ds_twr.corrected` logs coexist with successful-exchange JSON.
- USB receive only re-arms the endpoint: no command parsing. `CDC_Transmit_FS()` can return
  busy; logging is not a queued, loss-aware telemetry channel.

### ESP32 and host

- ESP32-S3 reads UART1 on GPIO 43/44 at 115200 baud, blinks GPIO 46 on activity,
  and bridges bytes bidirectionally to one TCP client (default port 3333).
- Wi-Fi credentials and TCP port are configured with `menuconfig` in ignored `sdkconfig`;
  the project uses standard ESP-IDF CMake files. UART text logging is not safe for arbitrary binary frames.
- The bridge has no authentication, encryption, discovery, buffering contract, or multi-client support.
  TCP-to-UART exists, but STM32 application commands are not parsed.
- The small host supports demo, TCP, USB serial, and replay; it serves a dependency-free UI,
  revisioned distance/position priors, rolling residual statistics, and NDJSON plus a manifest.
  Recordings include host timestamps/prior revisions, not firmware build identity.

Build/flash and monitor commands live in the [root README](../README.md);
host-only setup and source selection live in the [host README](../host/README.md).

## Data contract

### Current compact version 1

One UTF-8 NDJSON line follows each successful initiator exchange:

```json
{"type":"range","v":1,"board":1,"peer":2,"seq":42,"time_ms":41000,"raw_m":2.1234,"corrected_m":2.1012,"valid":true}
```

The host validates range records and ignores non-JSON debug lines. Raw range is immutable;
corrections and prior/model outputs are separate fields or files with revision provenance.
Breaking schema changes require coordinated firmware/parser changes, a version bump, and fixtures.
Unknown fields may be ignored. Do not confuse this implemented contract with the target below.

### Planned richer protocol

Keep newline framing at the host boundary: at expected research rates NDJSON fits 115200 baud
and is inspectable. Ignore unknown fields; reject unknown major schema versions.
A transitional parser for `ds_twr.distance`/`ds_twr.corrected` must retain unparsed source lines
for diagnosis; it is not the stable contract.

```json
{"schema_version":"1.0","type":"range","device_id":"uwb-01","boot_id":"8f31c2a4","sequence":1842,"device_time_us":91234001,"exchange_id":921,"peer_id":"uwb-02","raw_range_m":2.438,"corrected_range_m":2.421,"valid":true,"quality_flags":[]}
```

Required: device/peer identity, random-per-boot ID, monotonic sequence and device time,
exchange identity, raw range, validity, quality flags, type, and schema version.
The host adds `host_receive_time_utc`, `session_id`, and transport metadata without overwriting device time.

Desired diagnostics (omit unavailable values or use `null`; never invent them):

```json
{"rx_power_dbm":-71.2,"first_path_power_dbm":-74.8,"first_path_index":745.5,"cir_power":123456,"noise_std":18,"sts_quality":0.82,"clock_offset_ppm":-1.7,"temperature_c":31.4,"voltage_v":3.28}
```

| Message | Planned contents |
| --- | --- |
| `hello` | Firmware, protocol, board role, radio configuration, capabilities after boot |
| `health` | Counters, queue occupancy, resets, UART/TCP drops, radio errors |
| `event` | Boot/clock/radio reset, configuration applied, fatal error |
| `ack` | Validated host-command result when application control exists |

## Planned host and UI

Use one typed model/pipeline for live, replay, and batch data. Keep acquisition/radio work
independent of UI latency and model computation; analysis must run in tests/notebooks without a browser.
Assign stable identities to samples, priors, annotations, and configuration; expose missing values
and quality flags rather than silent substitutions.

The target separates acquisition, protocol, domain, analysis, storage, API, and browser assets.
Candidate tools: FastAPI/Pydantic, pandas/NumPy/SciPy, PyArrow Parquet, SQLite metadata, Plotly.
These are design options, not a requirement to replace the current small service or add unused layers.
Replay feeds the same sample pipeline as acquisition; recording preserves raw input, analysis
feeds the HTTP/WebSocket API, and the UI submits prior/annotation revisions.

The first screen is an experiment console:

- **Status:** transport state, last-sample age, session/recording state, valid/invalid rate,
  dropped sequences, firmware/schema versions.
- **Plots:** raw/corrected range against device time, expected range/uncertainty band, residual,
  rolling mean/stddev, robust outlier score, optional radio channels; units and stale/invalid state explicit.
- **Controls:** recording with required scenario metadata; timestamped `occlusion_start`,
  `occlusion_end`, `motion_start`, notes; LOS/NLOS/unknown intervals; closed-session export/manifest.

### Priors and derived samples

Support direct separation or node A/B `(x, y, z)` positions in metres, with frame/name,
position/distance uncertainty, effective time (now or selected sample/time), operator note,
and optional source/instrument. Expected distance is `||p_a - p_b||`; always use:

```text
residual_m = observed_range_m - expected_range_m
```

Saving creates an immutable revision applied immediately to subsequent samples.
Resolve by sequence/device time, not browser arrival order. Historical recomputation must be explicit,
not a rewrite of original samples. Target record:

```json
{"prior_id":"prior-0042","session_id":"session-20260928-01","effective_from_sequence":1843,"mode":"positions_3d","frame_id":"lab-a","node_a_m":[0.0,0.0,0.42],"node_b_m":[2.4,0.0,0.42],"expected_distance_m":2.4,"distance_std_m":0.005,"source":"laser_meter","note":"clear LOS"}
```

Direct-distance mode is `"distance"` and omits coordinates. Derived values reference raw samples:

```json
{"sample_id":"8f31c2a4:1842","prior_id":"prior-0041","pipeline_version":"online-0.1.0","expected_distance_m":2.4,"residual_m":0.038,"rolling_bias_m":0.031,"rolling_std_m":0.012}
```

### Planned archival storage

An atomically written, portable session directory is the archival unit, not a database server:

```text
session-20260928-01/
  manifest.json
  telemetry.ndjson       lossless received device messages
  samples.parquet        validated typed samples
  priors.ndjson          immutable revisions
  annotations.ndjson     events and interval labels
  checksums.sha256
```

SQLite may index sessions for the UI. Closing writes checksums and marks the manifest complete.
Large data and trained weights belong in managed external storage or Git LFS/DVC, not ordinary Git.
Create versioned manifests/configs/model metadata only when needed; keep recordings and weights untracked.

### Control and safety

Analysis priors stay on the host; device commands are only for operational settings such as
rate/radio configuration. Future commands must be allow-listed, validated, acknowledged, recorded,
and framed across TCP/UART. STM32 parsing belongs outside IRQ context and must not block or mutate
an in-progress exchange.

Bind the host to localhost by default; treat ESP32 TCP as trusted-lab-only until authenticated
or isolated. Keep credentials out of tracked source, impose message-length/numeric limits at
input boundaries, and never execute free-form device/UI strings.
