# UWB Lab Host

A small FastAPI service with a dependency-free browser dashboard. All commands
below run from the repository root.

## Setup and sources

Use Python 3.11+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './host[dev]'
make demo
```

Open <http://127.0.0.1:8000>. Stop with `Ctrl+C`.

| Source | Command / purpose |
| --- | --- |
| Demo | `make demo`: generated samples every 200 ms |
| Replay fixture | `make replay`: the committed synthetic session |
| ESP32 TCP | `bash scripts/run_dashboard.sh --source tcp --tcp-host "$UWB_BRIDGE_HOST"` |
| STM32 USB | `bash scripts/run_dashboard.sh --source serial --serial-port "$UWB_TX_USB"` |
| Dual USB | `bash scripts/run_dashboard.sh --source dual-serial --serial-port "$UWB_TX_USB" --responder-port "$UWB_RX_USB"` |
| Saved recording | `bash scripts/run_dashboard.sh --source replay --replay-file data/sessions/SESSION/telemetry.ndjson` |

Set the shell variables to your local addresses/ports; keep their values in ignored
`LAB_NOTES.md`, not tracked documentation. The CLI also accepts `UWB_TCP_HOST`,
`UWB_SERIAL_PORT`, and `UWB_RESPONDER_PORT` environment variables as defaults for
the corresponding flags. Missing required settings are reported before launch.
TCP defaults to port 3333; use `--tcp-port` to change it.
Run `.venv/bin/uwb-lab --help` for all options.

## Priors, plots, and recording

1. Select a source.
2. Expand **Geometry prior** and enter a measured distance or two 3D positions in
   metres, plus uncertainty/note as appropriate. Applying a prior affects subsequent
   residuals immediately; it does not rewrite earlier samples.
3. Use **Recording** to start/stop a named session, or use a batch as described below.

Residual means `corrected_m - expected_m`: positive means overestimating the
operator's reference. An unverified prior is not ground truth.
The host computes rolling bias and population spread over the last 20 residuals;
changing the prior resets that window.

Each plot independently selects raw/corrected/expected distance and
residual/bias/spread, with separate distance/error axes and auto/manual limits.
**Show Plot 2** adds another view; it starts with residual only.
Display settings are saved in this browser. **Reset display** restores them
without changing recording or the prior.

**Pause view** freezes plots/readouts, not acquisition, recording, or health
counters. **Last N samples** and plot size affect display only.
The browser buffers up to 2000 samples; a reconnect starts from the host's
up-to-300-sample snapshot. Neither limit truncates a recording or batch analysis.
Radio/prior panels and experiment controls can be collapsed without stopping work.

Manual recordings contain:

```text
data/sessions/<session-id>/
  manifest.json
  telemetry.ndjson
  priors.ndjson
  radio.ndjson
```

Recordings are ignored by Git. Raw values remain unchanged; expected distances
and prior revisions are stored alongside them. Replay feeds compact range records
through the current dashboard prior; use saved-batch analysis to retain the
original recorded expected distances.

## Live radio settings (two USB-connected boards)

Build/flash the updated STM32 firmware as `TxDebug` and `RxDebug`, then use
`--source dual-serial` with two distinct USB ports. See the [root guide](../README.md#configure-and-flash)
for flashing commands.

The **Radio / sampling** panel offers:

- Requested rate: 0.1-10 Hz (100-10000 ms period).
- Channel: 5 or 9; preamble: 64, 128, 256, 512, or 1024 symbols.
- Data rate: 850 kbps or 6.8 Mbps.
- Raw TX-power register, in hexadecimal: **not dBm** and not a lab-approved power range.

These are physical radio settings. Demo, replay, TCP, and single-USB modes cannot
apply them. The 10 Hz guard is not a verified throughput claim; requested rate and
observed successful-sample rate are shown separately.
See the [parameter reference](../docs/EXPERIMENTS_AND_VALIDATION.md#radio-and-sampling-parameter-reference)
for physical meaning and comparison advice.

**Apply to both boards** pauses both, applies and reads back both configurations,
then resumes RX before TX. Failures trigger rollback; an unconfirmed pair is not
deliberately resumed. Inspect individual errors/states after a disconnect: an
unreachable board's state is unknown, not assumed paused.
**Load current TX settings** only fills the form.

Settings are volatile and reset to firmware defaults on reboot. Existing graph
history remains. `radio.ndjson` records snapshots/outcomes; range records retain
their `radio_revision`. Revisions are local to a board boot, so record restarted
boards as separate sessions. Discarded USB lines are counted, not hidden.

## Repeated experiments and noise analysis

1. Connect a source and apply a prior.
2. In **Experiment setup**, choose name, run duration, integer run count, and interval.
3. Press **Start batch**. Prior/radio settings are snapshotted and locked, along with
   manual recording, throughout the batch including intervals.
4. Choose methods in **Analysis setup**, then select a batch and all/current/specific
   runs below the signal plots. All runs are selected by default.
5. **Recompute full closed dataset** saves results and provenance separately from telemetry.

The host owns scheduling: closing or refreshing the browser does not stop a batch.
**Stop batch**, disconnection, or host shutdown preserves incomplete outcomes.
An empty run fails explicitly. Batches do not physically reset conditions or prove
independence between runs.

| Method | What it shows |
| --- | --- |
| Statistics | Count, signed bias, sample standard deviation, RMSE, P95 absolute error |
| Histogram + Gaussian | Residual density and a single-Gaussian baseline |
| Spectrum | Welch PSD for regular timing; Lomb-Scargle power for gaps/irregular cadence |
| Gaussian mixture | A user-chosen 1-6-component fit, with weights, means, spreads, AIC/BIC |

All methods use **every eligible recorded individual sample** in the selection,
including an open run. Each job freezes input file lengths at submission.
There is one background worker with at most one replaceable pending job, so
expensive fits do not run on the acquisition event loop.

Choose raw or corrected range minus the **recorded** expected distance.
Invalid/missing-prior/non-finite samples are counted as exclusions; no outliers are
trimmed. Prior error is included. GMM components are not automatically LOS/NLOS
classes. The moving demo is not stationary sensor-noise ground truth.

Spectra stay within run boundaries. Welch density (m^2/Hz) and Lomb-Scargle power
(m^2) have different scaling and must not be directly compared.
Each run needs at least 32 eligible samples and increasing sequence/device time;
at roughly 1 Hz, allow at least 32 seconds and enough successful samples.
See [analysis semantics](../docs/EXPERIMENTS_AND_VALIDATION.md#repeated-run-console-and-initial-diagnostics)
for timing checks and estimator details.

Cards retain previous results during refresh; a stale-source notice identifies a
retained result when a method is unavailable. **Details** contains provenance and
method caveats. Display rounding to 0.001 m does not imply millimetre accuracy;
recordings and calculations keep their precision.

Batch layout:

```text
data/sessions/experiments/<batch-id>/
  manifest.json
  intervals.ndjson
  runs/<session-id>/{manifest.json,telemetry.ndjson,priors.ndjson,radio.ndjson}
  analyses/<result-id>.json
```

Interval measurements are retained but excluded from ordinary run analysis.
This is parsed telemetry, not a lossless capture of debug bytes or upstream losses.
Unfinished batches reopen as interrupted after a crash, without automatic resumption.
Saved analyses retain source hashes, outcomes, prior references, recipe, seed, and
numerical-library versions. They do not rewrite raw files.

The analysis API supports `mode: "cumulative"` (default) and `"full"` (save a
closed-data result). The obsolete `"live"` mode and `window_size` parameter were
removed; requests using them return validation errors. Older saved result JSON
remains readable. Existing browser preferences still migrate retired ACF settings.
Hard-refresh an already-open dashboard after updating the host.

## Antenna-delay calibration assistant

Use a fixed, accurately measured **LOS** separation and keep the setup still.
Enter known distance, target sample count (at least two), and tolerance; start
capture, then review corrected-range bias and the raw-range reference.
Capture stops at the target, or can be stopped/reset manually.

Recommendations are **suggestions only**: positive bias suggests increasing TX/RX
antenna delays equally on both boards; negative bias suggests decreasing them.
Within tolerance, no change is suggested. The optional DTU-per-cm hint is an
operator-supplied estimate, not an automatically calibrated conversion.

Manually adjust `QORVO_TX_ANT_DLY_DTU` and `QORVO_RX_ANT_DLY_DTU` in
[qorvo_port.c](../epuck-stm32-main/ThirdParty/qorvo_dw3/platform/qorvo_port.c),
rebuild/reflash, and collect a new validation run.
The assistant never writes to the boards. Capture is browser-local, lost on
reload, and not a saved calibration artifact; record a session separately if needed.
Demo/replay can exercise the UI but cannot validate physical calibration.

## Tests

Run `make verify` from the repository root. See the [development guide](../README.md#change-and-check-code)
for Python, Node.js, and native-C prerequisites.
