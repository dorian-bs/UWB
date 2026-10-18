# Architecture, Codebase, and Data Contract

**Current:** baseline documented on 2026-09-28; dual-USB controls added on 2026-10-13;
repeated experiments and noise analysis added on 2026-10-17.
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
  `POLL`/`ANSWER`/`FINAL`/`REPORT` exchange defaults to every 1000 ms; the initiator computes raw
  and corrected range from six timestamps after a matching report.
- Compile-time antenna-delay calibration collects statistics at a hard-coded known distance.
  The disabled range-bias hook currently returns unmodified range.
- `_write()` redirects `printf` to USB CDC; Qorvo logging also uses USART3 at 115200 baud.
  Readable `ds_twr.distance`/`ds_twr.corrected` logs coexist with successful-exchange JSON.
- USB CDC accepts bounded control commands, processed in the main loop rather than the
  USB receive callback. Commands pause/configure/resume the radio and return identified
  acknowledgements. See the USB control contract below; UART application control is not implemented.
  Ordinary range/debug telemetry is still not a loss-aware transport.

### ESP32 and host

- ESP32-S3 reads UART1 on GPIO 43/44 at 115200 baud, blinks GPIO 46 on activity,
  and bridges bytes bidirectionally to one TCP client (default port 3333).
- Wi-Fi credentials and TCP port are configured with `menuconfig` in ignored `sdkconfig`;
  the project uses standard ESP-IDF CMake files. UART text logging is not safe for arbitrary binary frames.
- The bridge has no authentication, encryption, discovery, buffering contract, or multi-client support.
  TCP-to-UART exists, but STM32 application commands are not parsed.
- The small host supports demo, TCP, USB serial, dual USB serial, and replay; it serves a dependency-free UI,
  revisioned distance/position priors, rolling residual statistics, and NDJSON plus a manifest.
  Recordings include host timestamps/prior revisions, not firmware build identity.
- Dual USB serial identifies the initiator and responder independently, coordinates radio
  updates with acknowledgement/readback/rollback, and records configuration events.
  Existing single-connection modes remain read-only for radio settings.

Build/flash and monitor commands live in the [root README](../README.md);
host-only setup and source selection live in the [host README](../host/README.md).

## Experiment and analysis host extension

`experiments.py` owns monotonic run/interval deadlines and a portable batch
manifest. It reuses `SessionRecorder` rather than replacing the recorder object
held by radio control. Prior, manual-recording, radio-apply and batch operations
share an API ownership guard; prior/radio changes stay locked during intervals.
Source disconnections latch an interruption immediately, including either USB
board's disconnect transition, independently of dashboard polling.

Batch storage under `data/sessions/experiments/<batch-id>/` contains a manifest,
separate `runs/<session-id>/` directories using the existing NDJSON session
format, inter-run samples in `intervals.ndjson`, and separate full-data results
under `analyses/`. Manifests/results use atomic replacement. Interrupted/failed
runs are not marked completed; unfinished archives reopen as interrupted.
Parsed invalid samples remain archived but are excluded from analysis.

`analysis.py` provides typed request/result shapes and NumPy/SciPy/scikit-learn
diagnostics. `analysis_jobs.py` defaults to cumulative analysis of every eligible
sample across the selected batch's runs, including an open run. It freezes each
recording's byte length at submission, directly loads records with their original
expected values, and executes one background job plus at most one replaceable
pending request. It never replays saved data through the current global prior.
The explicit API `live` mode retains its bounded window for older callers; the
dashboard uses `cumulative`, with no analysis sample cap.

Statistics show count, bias, sample standard deviation, RMSE and P95 absolute
error. Histogram/Gaussian and GMM fits use individual residuals, not run means or
histogram bins. The pooled PSD computes Welch on each complete run sample array
and combines the densities weighted by eligible sample count, on a shared
device-timed frequency grid. Welch requires at least 32 eligible samples each,
consecutive sequences, and device-time deltas within 5% of their run median and
the pooled median. Segment length is the smaller of 256 and the shortest run;
Hann windows overlap by half and are individually demeaned. No segment crosses
a run boundary.

If any run has missing sequences, irregular timing or a different cadence, the
whole selected population uses mean-subtracted Lomb-Scargle at the actual
within-run device timestamps. No samples are interpolated or dropped, and no
run boundaries are concatenated. Unnormalized per-run periodograms use `2/N`
scaling, then eligible-sample-weighted pooling; the chart is explicitly **power
(m^2)**, not Welch **density (m^2/Hz)**. The common frequency grid starts at the
reciprocal shortest run span and ends below the slowest run's median-cadence
Nyquist estimate, at about five points per shortest-span frequency scale
(256-4096 points). Frequency evaluation is chunked to bound temporary arrays
without capping samples. The grid step is not the physical resolving power.
Sequence resets/duplicates, non-increasing clocks, fewer than 32 eligible
samples per run or no common frequency band still produce explicit reasons.
Saved analysis version `noise-3` distinguishes this gap-aware estimator.
Autocorrelation is not currently exposed.

Results compose metrics, tables and labelled series (`line`, `bars`, `stems`,
`points`), rendered by dependency-free `analysis.js`; `experiments.js` supplies
the batch controls. Existing signal plot preferences and pause-view behavior
are separate. Numerical work does not run in the acquisition event loop.
Display-only rounding uses 0.01 m for distance/error readouts and roughly three
significant digits for other fractional analysis values; counts stay exact.
Stored samples, fits and exported results retain numerical precision. While a
selection refreshes, existing cards remain visible. A temporarily unavailable
method retains its last successful chart with a notice identifying the previous
source, rather than presenting it as an updated result.

| Endpoint | Implemented purpose |
| --- | --- |
| `POST /api/experiments/start`, `/stop` | Start/stop host-owned scheduling |
| `GET /api/experiments` | Discover saved batches |
| `GET /api/experiments/{batch_id}` | Batch manifest and individual run summaries |
| `GET /api/analysis/methods` | Method catalogue and request parameter schema |
| `POST /api/analysis/jobs` | Submit a typed selection/recipe; returns 202 with job ID/key |
| `GET /api/analysis/jobs/{job_id}` | Job state, explicit failures, typed numerical results |
| `GET /api/experiments/{batch_id}/analyses` | Discover full-data analysis artifacts |
| `GET /api/experiments/{batch_id}/analyses/{result_id}` | Read a saved reproducible result |

Saved results retain source SHA-256, selected runs/outcomes, raw/corrected field,
prior revisions, complete parameters/seed, cumulative/window/full-data mode, analysis
version and numerical-library versions. No raw fields are overwritten and the
firmware compact version-1 record is unchanged.

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

The optional additive `radio_revision` uint32 identifies the firmware configuration
active when a range was produced. The host preserves it through parsing, recording,
and replay. Version 1 and existing required fields are unchanged; older fixtures without
this field still work. Revisions restart at zero on boot; boot IDs remain future work.

### USB radio control (implemented, protocol version 1)

ASCII decimal integers, newline framing, request ID `id` in uint32 range:

```text
UWB <id> GET
UWB <id> PAUSE
UWB <id> SET <revision> <period_ms> <channel> <preamble> <data_rate> <tx_power>
UWB <id> RESUME
```

`SET` is permitted only while paused. Supported periods are 100-10000 ms, channels
5/9, preambles 64/128/256/512/1024 symbols, and rates 850/6800 kbps. TX power is
the raw uint32 DW3000 register, not a calibrated power in dBm. The UI displays it
in hexadecimal; the USB protocol uses decimal. RF/throughput validation on real boards
is still required, especially after channel changes.

Successful commands and rejections return current board state:

```json
{"type":"control","v":1,"id":42,"ok":true,"board":1,"role":"tx","revision":1,"period_ms":200,"channel":9,"preamble":128,"data_rate":6800,"tx_power":2694881440,"running":false}
```

Rejections additionally carry `error`. The host correlates IDs and validates
identity/state; uncorrelated replies never complete a pending operation.
Control replies are consumed before range parsing and are not malformed telemetry.
Write completion alone never means configuration succeeded.

`--source dual-serial` requires two distinct USB paths configured locally, with
`--serial-port` for the initiator and `--responder-port` for the responder. The host
reads both states, pauses both, sets a shared revision/settings, verifies both by
GET, and resumes RX before TX. Failures/cancellation trigger rollback using another
revision. If rollback/readback fails, the host attempts to pause both and exposes
unknown/unreachable states explicitly. This is coordinated best-effort control,
not an atomic transaction across disconnected hardware.

Control acknowledgement dispatch runs independently of telemetry delivery, with a
bounded 300-line telemetry queue retaining USB arrival times for observed-rate
calculation (queue draining is not mistaken for faster sampling). Slow dashboard sends have a deadline and failed
viewers are closed. Queue overflow or discarded queued input is counted per board
(`telemetry_dropped`) and displayed as discarded USB lines; it is not hidden as a
successful acquisition. Serial writes and rollback cleanup settle before command/
transaction locks are released, even if the requesting task is cancelled.

`POST /api/radio` accepts `sampling_hz`, `channel`, `preamble`, `data_rate`, and
`tx_power`; invalid values return 422, unavailable sources or failed coordination
return 409. `/api/status` includes per-board radio state, busy/error flags, and
`observed_hz` from recent successful sample arrivals (not device timing).
Session `radio.ndjson` stores initial snapshots and before/applied/failed/cancelled
events with board settings, revisions, host times, and errors. Raw measurements and
previous graph samples remain unchanged.

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
