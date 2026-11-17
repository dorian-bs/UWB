# Implementation Status

Updated **2026-10-31**. This is a student research tool, not a production platform.
The useful workflow is connect, check a prior, record repeatable runs, and inspect
the noise before adding more models.

## Working now

| Area | Available |
| --- | --- |
| Firmware | Two STM32 DS-TWR roles (`TxDebug`/`RxDebug`); initiator publishes compact version-2 range telemetry with additive DW3000 diagnostics |
| Transport | Direct USB, dual USB, or the ESP32 single-client UART/TCP bridge |
| Host | Demo and replay without boards; live acquisition, health counters, revisioned distance/3D-position priors |
| Recording | NDJSON sessions with manifests, prior changes, and radio snapshots/events |
| Dashboard | Two configurable signal plots, pause-view, display preferences, connection/recording health; residual and rolling bias keep equal-width signs (including rounded zero) to prevent sign-change jitter |
| Radio | Coordinated dual-USB rate/channel/preamble/data-rate/TX-power changes, readback and rollback |
| Experiments | Host-scheduled repeated timed runs and intervals; separate recordings and incomplete/interrupted outcomes |
| Analysis | Five core residual statistics, histogram with a selectable distribution fit (Gaussian, GMM, shifted log-normal, Student-t, Laplace, skew-normal) and model comparison, Welch or gap-aware Lomb-Scargle spectrum pooled over all runs of the batch |
| Noise modelling | `notebooks/uwb_noise_modelling.ipynb` (staged: anatomy, A/A noise floor, factor effects with run-level cluster-permutation/bootstrap tests, distribution shape, diagnostics, model scoreboard, generative sampler) on `uwb_lab.noise_data`/`noise_stats`/`noise_models`; condition labels in editable `notebooks/conditions.csv`; needs `pip install -e "host[notebook]"` |
| Calibration | Host-verified dual-USB fixed-LOS capture, evaluated antenna-delay preview/apply, readback/rollback and saved audit; runtime-only |
| Development | Hardware-free Python/API, Node UI, and native C harness tests; thin setup/build/flash wrappers |

Batch analysis uses all eligible individual recorded samples, not run averages
or a 2000-sample window. A job freezes file lengths at submission.
One worker coalesces pending requests; full closed-data results retain hashes,
parameters, prior references, and numerical-library versions (`noise-3`).
Raw measurements are never replaced with derived results.

Usage lives in the [host guide](../host/README.md); interfaces and ownership in
[architecture](ARCHITECTURE.md); scientific interpretation in
[experiments and validation](EXPERIMENTS_AND_VALIDATION.md).

## Student-accessibility cleanup

- Removed the obsolete windowed analysis API, duplicate live buffers, and
  full-selection/window-statistics merging. Cumulative and saved analysis now
  share the same recorded-data path.
- Kept the useful lab features and hardware acknowledgement/rollback safeguards.
  Core DS-TWR behavior is unchanged; telemetry schema now supports richer diagnostics for analysis.
- Hardware sources require explicit local addresses/ports instead of guessed
  machine-specific defaults. Demo/replay remain hardware-free.
- Dashboard smoke tests use fresh runtime state and pytest-managed temporary
  recordings rather than import-time environment changes and shared state.
- Environment checking includes numerical/API test dependencies and a native C compiler.
- Shortened the getting-started/status docs and removed a prescriptive multi-layer
  platform roadmap. Old design/implementation notes are historical, not requirements.

## Known limitations

- Noise modelling: re-mounting/re-rotating antennas shifts the bias by centimetres between
  replicate batches, while unrotated restarts agree to ~1 mm, so orientation effects need
  replicate batches before they are credible. Held-out-batch prediction is currently best with a
  global Student-t; condition-aware models do not yet beat it. The notebook's backlog lists next steps.

- This pass does not build, flash, or revalidate boards. The complete hardware
  chain and any changed RF settings still need explicit lab validation.
- Ranging defaults to roughly 1 Hz for bring-up. The 0.1-50 Hz command guard is
  the highest rate measured with ≥97% yield, not an accuracy claim.
- Sample loss at 10 Hz (~55% yield): recordings show contiguous `seq` and
  `telemetry_dropped` 0, so exchanges succeed but start too rarely. Firmware `time_ms`
  runs ~0.55x wall time at 100 ms: blocking 115200-baud UART debug prints inside the
  DW3000 interrupt (~1.1 KB/exchange, ~80 ms) starve SysTick, which also paces TX.
  Yield is roughly p/(p+80 ms). Diagnostic firmware (`DIAG`, see ARCHITECTURE.md) plus
  `make probe-loss` test this A/B. Hardware run (100 ms): baseline 55.7% yield (tick/wall 0.56,
  ~80 ms lost per poll); quiet ISR logs on both boards 98%; all fixes 100%; wall-clock
  scheduling alone 99.7% but ISR duty 81%; quiet on TX only 0% (RX reply latency 12 ms
  exceeds the TX receive timeout, 316 timeouts); quiet on RX only 98%. Baseline yield is
  71% at 200 ms and 87% at 500 ms. Confirmed. Fix implemented, flashed and verified on
  hardware (25 s runs): 100% yield at 100/200/500 ms (tick/wall 0.99, ISR duty 3.5%/1.7%/0.7%,
  RX reply latency 0.8 ms, no timeouts, `log_drop` 0); legacy flags 11 still give 53%.
  `qorvo_log.c` queues every log line (4 KB ring) and the main loop drains it, JSON range
  records get a reserved share and a longer USB wait, debug text is dropped first when full
  (`log_drop`); TX is scheduled from the DWT cycle counter. Per-exchange narration text is off
  by default (the range JSON carries the same data plus new `ra_dtu`/`rb_dtu`/`da_dtu`/`db_dtu`
  intervals); `DIAG 8` turns it back on. `DIAG` flags also re-enable the old behaviour for A/B
  (1 legacy log, 2 legacy tick, 4 mute text). Verify after flashing with
  `make probe-loss`: expect ~100% yield at 100/200/500 ms for the default and ~55% for
  legacy log+tick+trace (flags 11). Still open: ANSWER/FINAL use immediate TX against a 12 ms RX
  timeout, so check `rx_reply_latency_max_ms`; with trace on, the UART at 115200 baud cannot carry
  ~1.2 KB/exchange at 10 Hz, so some text lines are dropped by design.
- Above 10 Hz (flashed and measured on USB, 25 s per row, `data/loss_probe/20261117-083557.json`):
  the firmware `SET` period minimum is 10 ms and the UART to the ESP32 has its own lossy path
  (whole lines, skipped while busy; `uart_skip`/`uart_skip_json` in the diag line), so USB CDC is
  never held back by the 115200-baud UART. Exchange success is 100% at every rate with no
  timeouts and RX reply latency 0.8 ms. Yield: 50 ms 100%, 33 ms 97%, 25 ms 98%, 20 ms 98%,
  15 ms 96%, 10 ms 85% (ISR duty 7% to 30%; the shortfall is start-to-start period stretch
  with ms-granularity scheduling, not lost exchanges). The host guard is therefore 50 Hz
  (20 ms). The UART skips nothing at 50 ms but 195 JSON lines at 33 ms and more below, so the
  ESP32/TCP path is only complete up to about 20 Hz; use USB for faster runs. The ESP32 path
  above ~20 Hz was not recorded end to end through the dashboard.
- Only the initiator publishes ranges. Debug text and JSON share output; full
  CIR snapshots, boot IDs, and firmware build identity are still not recorded.
- Transport is not fully loss-aware. Parsed recordings cannot recover upstream
  losses or all original debug bytes. Radio revisions reset on reboot: start a
  new session after restarting a board.
- Priors are in memory until restart, though revisions are saved while recording.
  Replay uses the current prior; archived batch analysis uses recorded expected values.
- Automatic repetitions do not reset physical conditions or establish independent
  experiments. Moving priors, condition sweeps, labels, and model evaluation are not implemented.
- Calibration apply requires a one-time firmware update on both STM32 boards.
  Each successful SET is saved to STM32 flash sector 11 (0x080E0000) and restored at boot; the UI loads the boards' values on page load. A DFU reflash keeps the saved record. Other sources remain
  suggestion-only. Validate the updated pair on fresh LOS runs and other distances.
  Native/virtual-USB tests do not establish on-hardware ranging accuracy.
- Optional TX-interval jitter (`jitter_ms`, Radio / sampling panel) randomises the sampling cadence
  to break the narrowband, drifting-frequency noise seen at ≥20 Hz (suspected crystal-offset beat).
  Firmware and host are implemented and unit-tested but **not yet hardware-verified**: flash both
  boards, record at 40 Hz with jitter off and on, and compare the noise spectrum.
- ESP32 accepts one unauthenticated TCP client on a trusted lab network. Keep
  credentials and local connection details in ignored configuration/notes.
- The test environment currently emits an upstream Starlette/httpx deprecation
  warning. This cleanup does not change dependencies to chase that warning.

## Next steps

1. Validate the complete hardware chain and collect fixed-distance LOS recordings.
2. Calibrate antenna delays and compare bias/spread across a few distances.
3. Add simple timestamped LOS/NLOS labels and evaluate which additional diagnostics
   beyond the v2 compact set are worth storing.
4. Use the two-batch comparison view (residual deltas + transparent distribution overlays)
   to establish a simple baseline before considering HMMs, GPs, neural models, or downstream localization.

No database, plugin system, export stack, or extra service is required for these steps.

## Cleanup verification

- `make verify`: Ruff lint/format checks and all **93 pytest cases** pass,
  including Node UI tests, native C harnesses, and API smoke tests.
- `make check`: host dependencies, Node, native compiler, and installed firmware
  tools are detected successfully.
- Real demo and replay servers passed HTTP/WebSocket checks for assets, priors,
  recording, batches, and saved analysis using temporary session storage.
- Maintained documentation links/anchors resolve; changed host modules have no
  Pylance diagnostics. No firmware build, flash, or physical RF validation was run.
