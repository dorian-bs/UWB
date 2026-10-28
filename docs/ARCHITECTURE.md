# Architecture and Data Contracts

Current as of **2026-10-27**. This describes the code that exists, not a target
production architecture. See [status](IMPLEMENTATION_STATUS.md) for limitations.

## System and ownership

```text
STM32 initiator + DW3000 <--DS-TWR--> STM32 responder + DW3000
         | USART3 / 115200                 |
         v                                |
      ESP32-S3 --TCP:3333--> Python host <--+-- USB CDC
         STM32 --USB CDC-->      |
                                +--> browser dashboard
                                +--> NDJSON recordings --> replay/analysis
```

- **STM32:** radio timing, raw range calculation, firmware calibration, compact telemetry.
- **ESP32:** optional UART/TCP transport and Wi-Fi configuration; no science.
- **Host:** priors, recording, derived values, scheduling, analysis, and UI.

| File | Responsibility |
| --- | --- |
| [STM32 main.c](../epuck-stm32-main/Core/Src/main.c) | CubeMX startup and main loop; edit only project `USER CODE` blocks |
| [qorvo_port.c](../epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c) | DW3000 configuration, DS-TWR state machine, ranging/calibration, telemetry |
| [Qorvo platform folder](../epuck-stm32-main/ThirdParty/qorvo_dw3/platform/) | SPI/IRQ callbacks and USB/radio control helpers |
| [ESP32 main.c](../epuck-esp32-main/main/main.c) | UART1 GPIO 43/44 at 115200 baud, single-client TCP bridge, activity LED |
| [core.py](../host/uwb_lab/core.py) | Parser, geometry priors, rolling state, session recorder |
| [app.py](../host/uwb_lab/app.py) | Sources, acquisition, runtime state, FastAPI routes and WebSocket |
| [radio.py](../host/uwb_lab/radio.py) | Serial board acknowledgements, radio coordination/recovery, observed rate |
| [experiments.py](../host/uwb_lab/experiments.py) | Run/interval scheduling and batch manifests |
| [analysis.py](../host/uwb_lab/analysis.py) | Request/result shapes and numerical methods |
| [analysis_jobs.py](../host/uwb_lab/analysis_jobs.py) | Recording snapshots, one background worker, saved analysis |
| [static/](../host/uwb_lab/static/) | Plain HTML/CSS/JS dashboard; no build step |

`TxDebug`/`RxDebug` presets choose board roles. The initiator exchanges
`POLL`/`ANSWER`/`FINAL`/`REPORT` packets and computes range from six timestamps.
Its default poll period is 1000 ms, not a measured research-rate target.
USB/debug output coexists with UART telemetry. ESP32 UART1 uses TX GPIO 43,
RX GPIO 44; Wi-Fi credentials and TCP port live in ignored `sdkconfig`.

The host runs locally with demo, TCP, serial, dual-serial, or replay.
Hardware addresses/ports must be configured explicitly. The HTTP server binds
to localhost by default; the ESP32 bridge is trusted-lab-only.

## Compact range telemetry, version 1

The initiator emits one UTF-8 JSON line per successful exchange:

```json
{"type":"range","v":1,"board":1,"peer":2,"seq":42,"time_ms":41000,"raw_m":2.1234,"corrected_m":2.1012,"valid":true}
```

`raw_m` and `corrected_m` are metres; `time_ms` is device time in milliseconds.
The host ignores non-JSON debug text and counts malformed JSON-like lines.
It validates bounded, finite range values and adds a host UTC timestamp.
Unknown fields can be ignored.

Optional additive `radio_revision` is a uint32 identifying the active firmware
configuration. It is preserved by parsing, recording, and replay without changing
v1. Revisions reset on boot; record restarted boards as separate sessions until
boot IDs exist.

Raw data is immutable. Corrections produce separate values/files and must
identify their configuration or prior. Removing/renaming required fields means
coordinating firmware and parser changes, bumping the schema version, and
updating fixtures in the same change.

## Priors and storage

Distance priors store an expected separation; position priors compute
`distance(node_a, node_b)` from two `(x, y, z)` points in metres.
Each applied prior has an immutable revision, uncertainty, note, and timestamp.
New priors affect subsequent samples, reset the 20-residual rolling window, and
do not rewrite historical samples:

```text
residual_m = corrected_m - expected_m
```

The host holds 300 samples for reconnect snapshots; the browser holds up to
2000 for display. Neither limit truncates session files or batch analysis.
Pause-view affects only the browser.

Manual sessions under ignored `data/sessions/<session-id>/` contain
`manifest.json`, `telemetry.ndjson`, `priors.ndjson`, and `radio.ndjson`.
Manifests are atomically replaced and marked complete at recording close.
Telemetry keeps parsed measurements and their recorded prior-derived values,
not every original debug byte or upstream loss.
Replay uses compact range fields with the current prior; archived experiment
analysis instead reads recorded expected distances directly.

Batches use `data/sessions/experiments/<batch-id>/`, with a manifest,
`runs/<session-id>/` directories in the same format, `intervals.ndjson`,
and `analyses/<result-id>.json`.
Host monotonic deadlines drive scheduling, independent of browser lifetime.
Prior/radio/manual-recording controls are locked during runs and intervals. Batches accept `start_delay_s` (countdown before a run) and `manual_start` (each run waits in a `waiting` phase until `POST /api/experiments/next`); samples outside a run go to `intervals.ndjson` tagged with `phase`.
Stop/disconnect/shutdown preserves incomplete outcomes; unfinished saved batches
reopen as interrupted, without automatic resumption.

## Analysis

`AnalysisRequest` selects a batch, all/current/specific runs, raw/corrected
residual field, methods, and numerical parameters. Supported modes:

- `cumulative` (default): analyse all eligible recorded samples available at submission,
  including an open run.
- `full`: require closed selected runs and save the result separately from telemetry.

The retired `live` mode and `window_size` parameter are rejected rather than
silently switching populations. Old saved JSON can still be read.
Both supported modes share the same recording loader and numerical functions.
Each job freezes input file byte lengths and hashes those bytes; later appends
cannot change its population or fingerprint.

One background worker runs numerical work off the acquisition event loop, with
at most one replaceable pending job and a small in-memory job history.
Failures are exposed to the UI. Saved outputs include the recipe, source SHA-256,
run states, prior revisions, analysis version (`noise-3`), and library versions.
No database, task-queue service, or plugin registry is involved.

NumPy/SciPy/scikit-learn implement statistics, histogram/Gaussian, spectrum,
and GMM. Samples are pooled by count, not by averaging run means.
Spectra stay within run boundaries: regular timing uses Welch density;
gaps/irregular cadence use Lomb-Scargle power. See
[method semantics](EXPERIMENTS_AND_VALIDATION.md#repeated-run-console-and-initial-diagnostics)
for eligibility, scaling, and limitations.

## USB radio control (implemented, protocol version 1)

Newline-framed ASCII commands use decimal integer arguments and a uint32 request ID:

```text
UWB <id> GET
UWB <id> PAUSE
UWB <id> SET <revision> <period_ms> <channel> <preamble> <data_rate> <tx_power>
UWB <id> SET <revision> <period_ms> <channel> <preamble> <data_rate> <tx_power> <tx_ant_dly> <rx_ant_dly>
UWB <id> RESUME
```

`SET` requires a paused board. Periods: 100-10000 ms; channels: 5/9;
preambles: 64/128/256/512/1024; rates: 850/6800 kbps.
TX power is a raw uint32 DW3000 register, not dBm. The UI shows hexadecimal;
the protocol uses decimal. Supported numeric values are not RF certification.
The extended `SET` includes absolute unsigned 16-bit TX/RX antenna-delay registers
(0-65535 DTU); legacy `SET` preserves their current values. GET/ACK adds
`tx_ant_dly` and `rx_ant_dly` read from the DW3000, without changing protocol v1.
Missing delay fields identify older firmware and disable calibration apply only.
Radio reconfiguration preserves the current delays; reboot restores compile-time
defaults. There are no flash/OTP writes.

Acknowledgements and rejections include current board state:

```json
{"type":"control","v":1,"id":42,"ok":true,"board":1,"role":"tx","revision":1,"period_ms":200,"channel":9,"preamble":128,"data_rate":6800,"tx_power":2694881440,"running":false}
```

Rejections also carry `error`. Replies are matched by request ID and checked for
board role/state; write completion alone is not success.
Control parsing occurs in the STM32 main loop, not inside USB receive callbacks.
UART/TCP application control is not implemented.

Dual-serial requires two distinct USB paths. The host identifies both roles,
pauses both, applies a shared revision/configuration, verifies by `GET`, and
resumes RX before TX. Failure/cancellation triggers rollback with a new revision.
If the pair cannot be confirmed compatible, reachable boards are left paused
and unknown/unreachable states stay visible. This is best-effort coordination,
not an atomic transaction across disconnected hardware.

Serial writes and recovery finish before releasing locks, even on cancellation.
ACK dispatch is independent of the bounded 300-line telemetry queue.
USB arrival times are retained for observed-rate calculation.
Slow viewers have send deadlines and are disconnected on failure; discarded
queued USB lines increment `telemetry_dropped`.
`radio.ndjson` records snapshots and before/applied/failed/cancelled events.

## HTTP and browser interface

FastAPI's `/docs` lists the request schemas.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status`, `/api/samples` | Runtime health/state and recent samples |
| `POST /api/prior` | Apply a revisioned geometry prior |
| `POST /api/recording/start`, `/stop` | Manual recording |
| `POST /api/radio` | Apply `sampling_hz`, `channel`, `preamble`, `data_rate`, `tx_power` |
| `POST /api/calibration/start`, `/stop` | Host-verified dual-USB capture; known distance, sample target and tolerance |
| `POST /api/calibration/apply` | Apply the completed capture ID; no browser-supplied register values |
| `POST /api/experiments/start`, `/stop` | Host-owned batch scheduling |
| `GET /api/experiments`, `/api/experiments/{batch_id}` | Saved batches and run summaries |
| `GET /api/analysis/methods` | Method catalogue and parameter schema |
| `POST /api/analysis/jobs`, `GET /api/analysis/jobs/{job_id}` | Submit/poll analysis |
| `GET /api/experiments/{batch_id}/analyses`, `/analyses/{result_id}` | Discover/read saved results |
| `WS /ws` | Initial sample snapshot and subsequent samples |

Invalid radio inputs return 422; unavailable hardware or failed coordination
returns 409. Errors are shown explicitly rather than reported as success.
Dashboard assets use `Cache-Control: no-store`; already-open tabs need a refresh
after a host update.

Display preferences and non-dual-USB calibration suggestions are browser-local.
`calibration.py` owns dual-USB capture/provenance and evaluation. The host uses
parsed fresh telemetry only, locks conflicting operations during capture/apply,
and rejects identity/revision/connection changes, non-increasing clocks/sequences,
existing empirical corrections, incomplete captures and stale data.
Server status supplies counts, statistics, proposed register values and outcome;
refreshing the browser does not restart or abandon a host capture.

The equal-four-register method maps signed range bias to a rounded common
increment with `2 * 299702547 / (499.2e6 * 128)` metres per DTU. This calibrates
a pair offset, not independent board delays. The existing radio transaction
checks the full captured baseline immediately before writes, acknowledges and
verifies both boards, and restores per-board delays on recovery. Radio revision
changes identify subsequent measurements without changing the range-v1 schema.
Calibration intent and outcome, samples, method, parameters and register readback
are atomically saved to `data/sessions/calibrations/<id>.json`, independently of
manual recording. An unfinished `applying` audit is not a confirmed application.
USB connection epochs and telemetry clock checks reject observed restarts;
global firmware boot identity is still a limitation.
See the [host guide](../host/README.md) for operation.

## Extend only when needed

The next useful additions are boot/build identity, DW3000 diagnostics, and simple
timestamped LOS/NLOS labels. Define their fields and test recordings when they
are implemented. Parquet, SQLite, extra service layers, model registries, and
alternative chart frameworks are not prerequisites for this project.
