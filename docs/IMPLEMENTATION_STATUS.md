# Implementation Status and Roadmap

Baseline implementation snapshot: **2026-09-28**. Dual-USB radio controls: **2026-10-13**.
Repeated experiments and noise analysis: **2026-10-17**.
Pooled sample analysis and statistics display: **2026-10-18**.
This is a small vertical slice across phases 0-3, not full completion of those phases.
Contracts and code ownership live in [ARCHITECTURE.md](ARCHITECTURE.md);
scientific protocols, models, and metrics in [EXPERIMENTS_AND_VALIDATION.md](EXPERIMENTS_AND_VALIDATION.md).

## Working now

- Host-owned experiment batches schedule identical configured timed runs with
  optional intervals, snapshot a fixed distance/position prior and radio state,
  and preserve separate run sessions plus interval measurements. Prior/radio
  edits and manual recording are locked throughout a batch. Browser closure
  does not stop scheduling; stop/disconnect/shutdown preserves incomplete
  outcomes, and empty runs fail explicitly. Manifests use atomic replacement;
  saved unfinished batches reopen as interrupted without automatic resumption.
- Selectable noise-analysis cards provide the five core residual statistics
  (count, bias, sample standard deviation, RMSE, P95 absolute error),
  histogram/Gaussian baseline, pooled Welch PSD or timestamp-aware Lomb-Scargle
  power, and seeded
  configurable GMM fitting with parameters/convergence/AIC/BIC. Autocorrelation
  is removed for now. Raw/corrected residuals and current/saved runs share one
  Python implementation. All selected-batch runs are analysed by default,
  using every eligible individual sample recorded so far, not run averages or
  a 2000-sample analysis window. Each job freezes its input file lengths.
  Spectra combine sample-weighted within-run results on a common grid: Welch
  densities for regular timing, Lomb-Scargle power over actual timestamps if
  any samples are missing/irregular or cadences differ. No gaps are filled or
  compressed, and no runs are silently omitted. Resets, duplicate/non-increasing
  times and insufficient samples still produce explicit reasons.
  One background worker coalesces pending requests.
  Closed-file recomputation saves source hashes, recipe, prior references and
  numerical versions separately from raw data (analysis version `noise-3`).
  Distance/error readouts use display-only 0.001 m rounding; fractional readouts
  use three decimal places, tiny spectral values use scientific notation and
  counts stay exact integers. Residual statistics have a prominent signed-bias
  tile and separate error/spread tiles. Statistic values and their smaller units
  stay on one baseline; analysis tiles and live readouts reflow with available
  space instead of splitting numbers or wrapping units. Spectrum cards show the estimator,
  a compact metric strip and expandable details. Shared counts appear once
  above the cards; descriptions, provenance, method caveats and secondary GMM
  diagnostics are in Details or hover titles instead of repeating on plots.
  Cards remain visible during transitions/refreshes, with explicit stale-source
  notices if a method must retain its last successful result.
  Old saved preferences discard retired autocorrelation settings. Dashboard
  HTML/styles/scripts use `Cache-Control: no-store`; already-open tabs still
  need a hard refresh after a host update to replace their loaded JavaScript.
- Dashboard calibration assistant now provides fixed-LOS antenna-delay
  recommendations from corrected-range bias (with `raw_m` reference), but it
  remains suggestion-only: operators must manually edit STM32 firmware values,
  rebuild/reflash, and validate on a new run; no automatic board apply exists.
- `TxDebug`/`RxDebug` presets select STM32 board roles without source edits; initiator emits
  compact version-1 NDJSON after successful DS-TWR exchanges, preserving readable logs.
- ESP32-S3 bridges UART/TCP in both directions; Wi-Fi/port use `menuconfig`.
  Reverse transport exists; new STM32 control commands are USB-only, not UART/TCP.
- Python/FastAPI host supports demo, TCP, USB serial, dual USB serial, and recorded-file replay through the same pipeline.
- Radio / sampling controls support physical channel, preamble, data rate, raw TX power,
  and requested ranging rate with both STM32 boards connected over USB. The host pauses
  both radios, requires acknowledged settings/readback, resumes RX before TX, and attempts
  rollback on failure/cancellation. Unknown or paused states are visible. Demo/replay and
  single-board sources cannot change radio settings. Defaults stay at ~1 Hz.
  Requested cadence and observed successful sample rate are shown separately; the 0.1-10 Hz
  command range is not certified throughput. Both boards require updated firmware.
  Config snapshots/outcomes are recorded in `radio.ndjson`; additive `radio_revision`
  is preserved in range parsing/recording/replay without changing compact telemetry v1.
- Dependency-free browser UI shows range, expected distance, residual, rolling bias/spread,
  and health counters for samples, malformed lines, and missing sequences.
- Compact experiment console overlays selectable raw/corrected/expected distances and
  residual/bias/spread using separate distance and error axes. Display controls set sample
  window, plot height and lines/points. Both the main and optional second plot have all six
  trace choices and independent automatic/manual distance and error axis limits.
  Experiment controls can be collapsed. Radio / sampling and Geometry prior also have
  independent keyboard-accessible dropdown sections, initially collapsed on page load;
  closing them preserves form inputs without changing acquisition or recording.
  Recording remains visible. Preferences stay in this browser; hiding Plot 2
  retains its selections and limits. Older saved settings preserve Plot 1 and initialize
  Plot 2 to residual only with automatic axes.
  Pause freezes plots/readouts only, not acquisition, recording or health counters.
  Browser history is bounded at 2000 samples (reconnect snapshots contain up to 300);
  server rolling statistics still use the last 20 residuals, not the display window.
- Direct-distance and 3D-position priors apply immediately; immutable revision numbers identify derived samples.
  Sessions record telemetry, prior changes, and a small manifest under ignored `data/sessions/`.
- Parser, prior validation, residual, and recorder tests exist. The first integration pass had five focused tests;
  use the current suite, not this historical count, as the authoritative test inventory.
- Display-state, axis and preference tests use Node's built-in test runner through pytest,
  without browser libraries or frontend build tooling.
- Radio tests cover validation, acknowledged application, mismatched readback, lost ACKs,
  failed rollback, cancellation, and real pyserial acquisition on isolated virtual USB
  ports. In-flight writes and rollback retain their locks through cancellation;
  reachable boards restore independently, while incomplete pairs stay paused.
  USB ACK handling is independent of bounded telemetry delivery; slow viewers are
  closed, discarded lines are counted, and observed rates use preserved arrival times.
  Native C tests exercise firmware control without an Arm build or physical boards.
- Repeatable/idempotent macOS setup, environment checking, build/run/flash wrappers and top-level `make`
  commands support the project. Dependencies are declared in `host/pyproject.toml`; `.venv` holds the host
  environment. Setup includes ESP-IDF v5.5.5 and a user-local `~/.local/bin/idf.py` launcher.

### Pooled-statistics verification (2026-10-18)

- `make verify`: Ruff check/format and all 74 pytest tests pass, including Node
  UI tests, native firmware checks and HTTP smoke tests.
- Numerical regressions cover 3200 individual samples from unequal-length runs,
  Gaussian/GMM sample weighting, pooled sample-level spectra, reset rejection,
  and an open recording's immutable submission-time snapshot beyond 2000 samples.
- Gap-aware spectrum regressions recover a known tone from missing/jittered
  timestamps, verify sample weighting and finite constant-signal power, and
  exercise cumulative/saved analyses after a sample loss. The reported hardware
  recording was also recomputed read-only: all five runs and all observed
  samples yield a finite current spectrum rather than a stale-result notice.
- Browser checks on isolated demo data: two 40-sample runs produce one 80-sample
  analysis and spectrum; cards never become empty during the transition.
  Removal of ACF, raw/corrected selection, saved
  full-data recomputation and 390-pixel layout without overflow were checked.
- Presentation checks cover three-decimal readouts, signed bias/error tiles,
  estimator badges and shared counts for both Welch and Lomb-Scargle, notes expansion
  surviving result refreshes, unwrapped numeric tiles at desktop width and
  the two-column residual layout at 390 pixels without horizontal overflow.
- Compact graph checks verify short metric labels/legends, no redundant
  single-curve captions, provenance and secondary diagnostics under Details,
  and numerical warnings remaining visible outside methodology notes.
- Inline-unit browser checks cover all 14 analysis/live value-unit pairs at nine
  widths from 320 to 1440 pixels, including wider signed test values: no split
  numbers, wrapped units, tile overflow or page overflow.
- Cache regressions cover all five dashboard asset routes. Batch-start analysis
  restores old saved settings without submitting `acf`, `max_lag` or the retired
  analysis-window preference, while preserving supported selections and parameters.
- The pre-existing Starlette/httpx deprecation warning remains. No hardware or
  RF precision/accuracy claim is implied by these host-only checks.

### Experiment-workspace verification (2026-10-17)

- `make verify`: Ruff check/format and all 61 pytest tests pass, including
  the Node UI tests, native firmware checks and real HTTP smoke tests.
- Browser checks: two runs with an interval, pooled full-data analysis, separate
  per-run temporal results, raw/corrected selection, method toggles, saved-data
  reopening after restart, and 390-pixel layout without page overflow.
- Host scheduling completes both runs while the dashboard is navigated away.
- Code-review regressions cover failed run finalization and delayed partial
  results for explicitly selected runs at closure.
- One pre-existing Starlette/httpx deprecation warning remains. These checks do
  not certify RF behavior, hardware rates, or physical statistical independence.

### Previously verified (hardware not reverified by this host change)

- STM32 `TxDebug`/`RxDebug` and ESP32-S3 builds with ESP-IDF v5.5.5.
- Host tests; demo streaming, prior updates, recording, and replay in the browser.
- Dashboard at a 390-pixel mobile viewport without horizontal overflow.

### Known limitations

- Hardware-chain bring-up remains to be confirmed: flash both nodes/bridge, configure local Wi-Fi,
  verify ESP32 UART pins, and discover its lab IP. Keep local addresses in ignored `LAB_NOTES.md`.
- Only the initiator publishes ranges; debug text and JSON share output. No radio diagnostics or
  recorded firmware build identity yet; transport is not fully loss-aware.
- Default ranging is ~1 Hz for bring-up, not a measured research rate. Runtime radio changes
  and achievable rates still need hardware validation; no build/flash is implied by host tests.
  Radio revisions reset on board reboot; record rebooted runs as separate sessions until
  boot IDs exist. Priors reset on host restart unless recorded.
- Batch conditions are configured identically, not physically reset or certified
  independent. Moving/multiple priors, automated condition sweeps and formal
  model validation remain future work. Archives retain parsed measurements,
  not all original debug bytes or samples lost upstream. The demo includes
  changing geometry and is not a stationary noise reference.
- ESP32 accepts one unauthenticated client on a trusted lab network; local credentials must be set before flashing.

## Immediate priorities

1. Test the complete hardware chain, collect fixed-distance LOS recordings, and calibrate antenna delays.
2. Add timestamped LOS/NLOS event labels and DW3000 quality diagnostics.
3. Collect independent repeated recordings and evaluate the implemented noise
   diagnostics before selecting more complex models or calibration corrections.
4. Establish a simple LOS/NLOS baseline before HMM/change-point comparisons.
   GP/autoregressive noise models and downstream localization evaluation depend on sufficient data.

## Integration history

Before the first pass, STM32 already had CubeMX startup, DW3000 SPI/reset/chip-select/IRQ support,
interrupt-driven DS-TWR, six-timestamp initiator ranging, optional calibration statistics,
an inactive bias hook, USB/USART3 diagnostics, and a main-loop status LED. Board ID/role required
manual `main.c` macros; there was no stable telemetry or host-command parser.

ESP32 already had a station connection, UART1 GPIO 43/44 at 115200 baud, one-client TCP:3333,
and GPIO 46 activity indication. Credentials were hard-coded; TCP input was discarded except for
disconnect detection. The first pass added role presets, structured telemetry, configurable
credentials, reverse byte forwarding, the host/UI/recording/replay, supporting docs, and setup tooling.

## Ordered roadmap

Each layer must be testable from recordings before relying on the next hardware feature.
Reassess relative effort and target cadence after phase 0 measurements.

### Phase 0 - Baseline and repository hygiene

**Work:** build/flash explicit board-role presets; keep credentials in untracked configuration/provisioning;
record firmware commits, board IDs, antenna delays, radio settings, and toolchains.
Capture **at least 30 minutes** of fixed-distance LOS logs across several distances.
Measure exchange rate, malformed output, USB/UART loss, CPU headroom, and achievable cadence.
Verify quick-start commands and physical connections on hardware.

**Acceptance:** clean checkout builds both roles; baseline recording/manifest reproducible without
source edits; one-second cadence and transport reliability measured.

### Phase 1 - Structured firmware telemetry

**Work:** implement shared fixtures/contracts for `hello`, `range`, `health`, `event`; separate debug
from telemetry; add boot/sequence/device-time/exchange/peer identity, validity, quality flags,
and available DW3000 diagnostics with units/driver APIs. Queue output outside IRQ callbacks and
report queue/transport drops. Make period a build setting before safe runtime control.

**Acceptance:** parser validates **10,000 recorded messages** without ambiguous boundaries;
forced radio errors/congestion yield flags/counters; debug can be disabled without losing telemetry.

### Phase 2 - Acquisition, recording, replay

**Work:** lock Python dependencies; unify TCP/serial/replay behind an async interface;
typed validation and malformed-line quarantine; manifests, NDJSON, Parquet, annotations, checksums;
bounded queues and explicit backpressure/drop metrics. Deterministic fixtures cover valid/missing fields,
disconnects, duplicates, gaps, and reboot.

**Acceptance:** live/replay produce identical domain records; interrupted recording cannot corrupt
closed sessions; fixture replay has deterministic counts, flags, timestamps.

### Phase 3 - Experiment console and dynamic priors

**Work:** FastAPI health/session/prior endpoints and telemetry WebSocket; status/plots/connection/recording;
distance/3D prior forms with units, uncertainty, frame, note, effective time; sequence/time resolution;
events and LOS/NLOS/unknown intervals; CSV/Parquet/session export and replay selector.
Bound browser history while preserving full recording.

**Acceptance:** geometry affects subsequent residuals within **one UI refresh** without restarting;
earlier samples keep revisions unless explicitly recomputed offline; live/replay UI parity;
stale/invalid/disconnected/recording-failure states prominent.

### Phase 4 - Baseline analysis and calibration

**Work:** implement the [ranging baselines](EXPERIMENTS_AND_VALIDATION.md#baselines);
histograms, Q-Q, autocorrelation/partial autocorrelation, stationarity, residual-versus-diagnostic plots;
antenna-delay/systematic-bias calibration on dedicated sessions; uncertainty plots/intervals;
versioned reports from manifest and analysis config.

**Acceptance:** fixed baseline outputs on common splits; calibration only from calibration/training data;
config reruns reproduce figures/metrics within documented numerical tolerance.

### Phase 5 - LOS/NLOS and temporal models

**Order:** residual/rolling threshold and change point -> causal logistic/tree ->
AR/ARIMA/state space -> two-state HMM (check state interpretation) ->
GP bias/uncertainty (sparse if needed) -> compact temporal neural model only if data/baselines justify it.

**Work:** common fit/predict/evaluate interface; explicit causal/offline feature modes;
validation-only tuning; track model artifact, code revision, dataset manifest, feature schema, seeds;
measure live inference latency/memory.

**Acceptance:** session-grouped confidence intervals; report the full
[range and LOS/NLOS metrics](EXPERIMENTS_AND_VALIDATION.md#metrics), including predictive coverage,
class-wise results, transition delay, and false alarms per minute.

### Phase 6 - Independent validation

**Work:** freeze model/preprocessing before opening held-out data; test unseen runs and at least one
changed environment/obstruction arrangement; compare raw/baseline/selected model. Stress long runs with
disconnect/reconnect, prior updates, reboot, storage pressure. Document failures and out-of-distribution
conditions; optionally benchmark a simple 1D/2D localization filter.

**Acceptance:** fresh-environment experiment-to-report workflow; distinguish statistical from practical
localization improvement; independently auditable raw sessions/manifests.

## Cross-cutting verification and risks

| Surface | Tests |
| --- | --- |
| Firmware | Serialization, numeric limits, sequence/reset, overflow, role configuration |
| Host | Parser, timestamps, prior resolution, residual sign, rolling windows, schema migration |
| Integration | TCP fragmentation/reconnect, malformed lines, replay parity, start/stop recording |
| UI | Desktop/mobile, stale states, invalid forms, bounded plots, prior revision display |
| Scientific | Split leakage, deterministic preprocessing, metric fixtures, uncertainty calibration |

| Risk | Mitigation |
| --- | --- |
| Logging perturbs timing | Queue/serialize outside IRQ; benchmark logging on/off |
| Clock drift | Retain monotonic time/boot ID; estimate alignment rather than overwrite |
| Prior mistaken for truth | Record source/uncertainty; verify independently |
| Inflated scores / ambiguous NLOS labels | Whole-session/trajectory/environment splits; timestamp obstruction events and synchronization; retain `unknown` |
| Small-data neural overfit | Strong baselines and learning curves first |
| ESP32 disconnect loss | Counters, bounded buffers, USB fallback, visible gaps |
| Credential leakage | External provisioning; rotate any credentials previously embedded in firmware |

Delivery checkpoints: **A** reproducible builds/baseline dataset; **B** telemetry/loss-aware recorder/replay;
**C** revisioned priors/annotations UI; **D** characterization report; **E** frozen independent model comparison.
Update this document when a feature becomes usable or a limitation disappears.
