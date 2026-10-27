# Implementation Status

Updated **2026-10-27**. This is a student research tool, not a production platform.
The useful workflow is connect, check a prior, record repeatable runs, and inspect
the noise before adding more models.

## Working now

| Area | Available |
| --- | --- |
| Firmware | Two STM32 DS-TWR roles (`TxDebug`/`RxDebug`); initiator publishes compact version-1 range telemetry |
| Transport | Direct USB, dual USB, or the ESP32 single-client UART/TCP bridge |
| Host | Demo and replay without boards; live acquisition, health counters, revisioned distance/3D-position priors |
| Recording | NDJSON sessions with manifests, prior changes, and radio snapshots/events |
| Dashboard | Two configurable signal plots, pause-view, display preferences, connection/recording health |
| Radio | Coordinated dual-USB rate/channel/preamble/data-rate/TX-power changes, readback and rollback |
| Experiments | Host-scheduled repeated timed runs and intervals; separate recordings and incomplete/interrupted outcomes |
| Analysis | Five core residual statistics, histogram/Gaussian, Welch or gap-aware Lomb-Scargle spectrum, seeded GMM |
| Calibration | Browser-local fixed-LOS antenna-delay suggestions; firmware changes remain manual |
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
  No firmware behavior or telemetry schema changed.
- Hardware sources require explicit local addresses/ports instead of guessed
  machine-specific defaults. Demo/replay remain hardware-free.
- Dashboard smoke tests use fresh runtime state and pytest-managed temporary
  recordings rather than import-time environment changes and shared state.
- Environment checking includes numerical/API test dependencies and a native C compiler.
- Shortened the getting-started/status docs and removed a prescriptive multi-layer
  platform roadmap. Old design/implementation notes are historical, not requirements.

## Known limitations

- This pass does not build, flash, or revalidate boards. The complete hardware
  chain and any changed RF settings still need explicit lab validation.
- Ranging defaults to roughly 1 Hz for bring-up. The 0.1-10 Hz command guard is
  not verified throughput or an accuracy claim.
- Only the initiator publishes ranges. Debug text and JSON share output; no
  RSSI/first-path/CIR diagnostics, boot IDs, or firmware build identity are recorded yet.
- Transport is not fully loss-aware. Parsed recordings cannot recover upstream
  losses or all original debug bytes. Radio revisions reset on reboot: start a
  new session after restarting a board.
- Priors are in memory until restart, though revisions are saved while recording.
  Replay uses the current prior; archived batch analysis uses recorded expected values.
- Automatic repetitions do not reset physical conditions or establish independent
  experiments. Moving priors, condition sweeps, labels, and model evaluation are not implemented.
- Calibration capture is browser-local and suggestion-only; verify manual firmware
  adjustments on a fresh physical LOS run.
- ESP32 accepts one unauthenticated TCP client on a trusted lab network. Keep
  credentials and local connection details in ignored configuration/notes.
- The test environment currently emits an upstream Starlette/httpx deprecation
  warning. This cleanup does not change dependencies to chase that warning.

## Next steps

1. Validate the complete hardware chain and collect fixed-distance LOS recordings.
2. Calibrate antenna delays and compare bias/spread across a few distances.
3. Add simple timestamped LOS/NLOS labels and useful DW3000 diagnostics when needed.
4. Compare independent recordings using the existing analysis. Add a simple baseline
   before considering HMMs, GPs, neural models, or downstream localization.

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
