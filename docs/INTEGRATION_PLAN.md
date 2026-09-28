# Integration Plan

The phases are ordered to make each new layer testable from recorded input before depending on the next hardware feature. Estimates are relative and should be adjusted after Phase 0 measurements.

## Phase 0: Baseline and repository hygiene

Goal: produce a repeatable baseline before changing protocol or timing.

Tasks:

- Build and flash distinct initiator and responder images from explicit CMake options/presets instead of editing `main.c` macros.
- Move ESP32 Wi-Fi credentials to untracked configuration or provisioning.
- Record firmware commit, board IDs, antenna delays, radio settings, and toolchain versions.
- Capture at least 30 minutes of current logs at several fixed distances in LOS.
- Measure successful exchange rate, malformed output, USB/UART loss, CPU headroom, and achievable ranging cadence.
- Add a root-level quick-start README once commands and physical connections are verified on hardware.

Acceptance criteria:

- A clean checkout can build both board roles with documented commands.
- A baseline recording and manifest can be reproduced without source edits.
- The current one-second cadence and transport reliability have measured values.

## Phase 1: Structured firmware telemetry

Goal: expose one stable, machine-readable record per ranging exchange.

Tasks:

- Define `hello`, `range`, `health`, and `event` messages from `ARCHITECTURE.md` in a shared protocol document/test fixture.
- Separate debug logging from telemetry output.
- Add boot ID, sequence, monotonic timestamp, exchange ID, peer ID, raw range, validity, and quality flags.
- Expose available DW3000 receive diagnostics and document units/driver APIs.
- Queue telemetry outside IRQ callbacks; count and report queue/transport drops.
- Make range period a build configuration first. Add runtime control only after the command path is safe.

Acceptance criteria:

- A host parser validates 10,000 recorded messages with no ambiguous sample boundaries.
- Forced radio errors and output congestion produce flags/counters rather than silent data loss.
- Debug output can be disabled without removing telemetry.

## Phase 2: Host acquisition, recording, and replay

Goal: create an immutable and testable data pipeline before building model logic.

Tasks:

- Create the Python package and lock dependencies.
- Implement TCP, USB serial, and file-replay adapters behind one async interface.
- Validate protocol messages with typed models and quarantine malformed lines.
- Add session manifests, NDJSON capture, Parquet conversion, annotations, and checksums.
- Add bounded queues and explicit backpressure/drop metrics.
- Create deterministic fixtures for valid data, missing fields, disconnects, duplicate sequences, sequence gaps, and device reboot.

Acceptance criteria:

- Live and replay adapters produce the same domain records.
- Terminating the recorder unexpectedly cannot corrupt an already closed session.
- Replaying a fixture yields deterministic sample counts, flags, and timestamps.

## Phase 3: Live UI and dynamic priors

Goal: provide the minimum complete experiment console.

Tasks:

- Implement FastAPI health/session/prior endpoints and a telemetry WebSocket.
- Build the status bar, live range/residual plots, connection state, and recording controls.
- Add 3D position and direct-distance prior forms with units, uncertainty, frame, note, and effective time.
- Version each prior update and associate samples by sequence/time.
- Add event markers and interval LOS/NLOS/unknown labels.
- Add CSV/Parquet/session export and a replay selector.
- Bound chart history in the browser while preserving complete recording on disk.

Acceptance criteria:

- Changing geometry updates expected distance and subsequent residuals within one UI refresh without restarting acquisition.
- Earlier samples retain their original prior revision unless the user explicitly requests an offline recomputation.
- The UI behaves identically with a live source and a replayed session.
- Stale data, invalid range, disconnect, and recording failure are prominent and unambiguous.

## Phase 4: Baseline analysis and calibration

Goal: establish interpretable benchmarks before advanced models.

Tasks:

- Implement no-correction, global-bias, distance-binned Gaussian, and rolling robust baselines.
- Produce residual histograms, Q-Q plots, autocorrelation/partial autocorrelation, stationarity checks, and residual versus diagnostic plots.
- Estimate antenna delay and systematic bias on calibration-only sessions.
- Add uncertainty calibration plots and prediction intervals.
- Generate a versioned report from a session manifest and analysis config.

Acceptance criteria:

- Every advanced model can be compared to fixed baseline outputs on the same splits.
- Calibration parameters are learned only from designated calibration/training data.
- Re-running an analysis config reproduces metrics and figures within documented numerical tolerance.

## Phase 5: LOS/NLOS and temporal models

Goal: evaluate model families in increasing order of complexity.

Implementation order:

1. Threshold and change-point baselines on residual/rolling statistics.
2. Logistic regression or tree baseline on causal rolling features.
3. AR/ARIMA or state-space residual model.
4. Two-state HMM with LOS/NLOS interpretation checked after fitting.
5. Gaussian process for bias and uncertainty versus geometry/diagnostics, using sparse methods if needed.
6. Compact temporal neural model only if dataset size and baseline results justify it.

Tasks:

- Provide one common fit/predict/evaluate interface.
- Make causal and offline feature modes explicit.
- Tune hyperparameters on validation sessions only.
- Track model artifact, code revision, dataset manifest, feature schema, and random seeds.
- Measure inference latency and memory for live candidates.

Acceptance criteria:

- Results include confidence intervals across independent sessions, not only pooled samples.
- LOS/NLOS evaluation reports class-wise precision/recall, F1, AUROC/AUPRC where appropriate, transition delay, and false alarms per minute.
- Range correction reports bias, MAE, RMSE, high-percentile absolute error, and predictive interval coverage.

## Phase 6: Independent validation and integration

Goal: demonstrate generalization and useful downstream impact.

Tasks:

- Freeze the selected model and preprocessing before opening the held-out test set.
- Validate on unseen runs and at least one changed environment or obstruction arrangement.
- Compare raw range, baseline correction, and selected model.
- Run long-duration tests with disconnect/reconnect, prior changes, device reboot, and storage pressure.
- Document limitations, failure cases, and conditions outside the training distribution.
- Optionally evaluate a simple 1D/2D localization filter to quantify downstream value.

Acceptance criteria:

- The complete experiment-to-report path runs from a fresh environment using documented commands.
- The final report distinguishes statistical improvement from practical localization improvement.
- Raw sessions and manifests remain independently auditable.

## Cross-cutting tests

- **Firmware:** protocol serialization, numeric limits, sequence/reset behavior, queue overflow, role configuration.
- **Host unit:** parser, timestamp handling, prior resolution, residual convention, rolling windows, schema migrations.
- **Integration:** simulated TCP fragmentation, reconnect, malformed lines, replay parity, start/stop recording.
- **UI:** desktop/mobile layout, stale states, invalid form values, plot memory bound, prior revision display.
- **Scientific:** split leakage checks, deterministic preprocessing, metric fixtures, uncertainty calibration.

## Principal risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Debug logging perturbs radio timing | Queue telemetry and serialize outside IRQ context; benchmark with logging on/off |
| Host and device clocks drift | Keep monotonic device time and boot ID; estimate alignment instead of overwriting time |
| UI prior is mistaken for ground truth | Store source and uncertainty; use `prior` terminology until independently verified |
| Random sample splits inflate results | Split by complete session/trajectory/environment |
| NLOS labels are ambiguous | Record obstruction events and synchronization method; retain `unknown` state |
| Neural model overfits a small dataset | Require strong baselines and learning curves before adoption |
| ESP32 loses data during disconnect | Device/bridge counters, bounded buffers, local USB fallback, visible gaps |
| Credentials leak from firmware | Provision externally and rotate the currently embedded credentials |

## Suggested delivery checkpoints

- **Checkpoint A:** repeatable DS-TWR builds and baseline dataset.
- **Checkpoint B:** structured telemetry plus loss-aware recorder/replay.
- **Checkpoint C:** live UI with revisioned priors and annotations.
- **Checkpoint D:** baseline characterization report.
- **Checkpoint E:** model comparison on frozen independent test sessions.
