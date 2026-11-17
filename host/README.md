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

- Requested rate: 0.1-50 Hz (20-10000 ms period). Measured on USB: 100% exchange success and
  ≥97% yield down to 20 ms; 15 ms gives 96% and 10 ms only 85%, so 50 Hz is the cap. The ESP32
  UART/TCP link (115200 baud, ~35 ms per record) is only complete up to about 20 Hz; above that
  the firmware skips lines for the UART and only USB (`dual-serial`) carries every sample.
- Random sample timing (jitter): tick the box and set ± ms to add a uniform random offset to every
  exchange interval (mean rate unchanged; at least 10 ms must remain between exchanges, so at most
  period − 10 ms, capped at 50 ms). Use it when high-rate noise looks like a drifting sine: it turns
  an aliased periodic error into ordinary white noise. Needs the updated firmware on both boards.
- Channel: 5 or 9; preamble: 64, 128, 256, 512, or 1024 symbols.
- Data rate: 850 kbps or 6.8 Mbps.
- TX power: one of seven preset `TX_POWER` register values, nominally 3 dB apart from the maximum down to -18 dB. They are relative steps, **not calibrated dBm** and not a lab-approved power range.

These are physical radio settings. Demo, replay, TCP, and single-USB modes cannot
apply them. The 50 Hz guard is the highest rate measured with ≥97% yield, not a certified throughput claim; requested rate and
observed successful-sample rate are shown separately.
The **Samples per second** tile shows the observed rate next to the rate the TX board is configured for (for example `9.98 / 10 Hz`).
The **Samples/s** and **Requested rate** plot traces (rate axis, from zero) draw the same over time: Samples/s is measured over the
last 10 arrivals, so it reacts within a second or two. Both are plot-only values the host adds when streaming samples; recordings keep the raw records.
The host restarts the rate windows whenever the channel, preamble, data rate or period changes, because the pause while the boards apply
the new setting would otherwise depress the reading for up to 100 samples.
The radio panel also shows the share of recent ranging attempts (from the firmware exchange counter) that produced a sample.
See the [parameter reference](../docs/EXPERIMENTS_AND_VALIDATION.md#radio-and-sampling-parameter-reference)
for physical meaning and comparison advice.

The noise analysis **From run # / To run #** fields (blank = no limit) restrict the analysis to a range of runs, e.g. to drop bad runs at the start or end.
The **Exclude runs** drop-down has a checkbox per run to leave individual runs out of the analysis.
The **Compare with** selector overlays two batches for residual comparison: it uses all recorded runs from both batches,
adds residual-metric deltas (B - A), and overlays both histograms/fitted curves with transparency.
When comparison is active, run-range and per-run exclusion controls are disabled.
Use **Show fitted curves in batch comparison** to toggle fitted overlays on top of the histogram bars.
Set **Distribution model** to **Auto (best AIC per dataset)** to let each compared batch pick its own best model.
In the noise analysis, the collapsible **Experiment configuration** section lists the selected dataset's
settings as recorded in its manifest: batch settings, prior, and the TX/RX radio configuration.

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
   Optionally select a second batch in **Compare with** to compare both full batches.
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

### Apply the evaluated calibration to hardware

1. Update **both STM32 boards** with the firmware containing antenna-delay USB
   control support (one-time build/flash using the root guide). Connect both over
   USB and run the dashboard in `dual-serial` mode. Old firmware disables apply;
   ordinary radio controls still work.
2. Fix the boards at an accurately measured antenna-to-antenna LOS distance,
   clear of nearby reflectors. Keep orientation, mounting and radio settings fixed.
   Enter distance, tolerance and 2-10000 target samples, then **Start capture**.
   Dual-USB captures are collected by the host and survive browser reloads.
3. Review the mean error and proposed old/new TX and RX antenna delays for each
   board. **Apply evaluated delays to both boards** pauses ranging, writes and
   reads back the registers, then resumes the responder before the initiator.
   Failure/cancellation attempts rollback; inspect any unknown or paused states.
4. Capture again to check residual bias, then validate at other distances on
   independent runs. A low bias at the calibration distance is not proof of
   accuracy everywhere or lower random noise.

The update is **runtime-only**: each board returns to its firmware defaults after
restart. Subsequent UI adjustments require no reflashing; no flash or OTP is
written. The calibration identifies a **pair offset**, not independent per-board
delays. Recalibrate/validate after channel, mounting or orientation changes.

Positive bias increases all four TX/RX registers equally; negative bias decreases
them. The host computes `delta = round(error_m / (2 * c * DWT_TIME_UNITS))`, with
`c = 299702547 m/s` and `DWT_TIME_UNITS = 1 / (499.2e6 * 128) s`, matching firmware.
One DTU on all four registers changes the pair range by about **9.38 mm**.
Within tolerance or below rounding to one DTU, no update is sent.
The editable manual DTU hint is **never used for hardware apply**. If raw and
corrected ranges already differ, hardware application is blocked to avoid
calibrating an empirical correction into the timestamps.

Calibration and radio/prior/experiment changes cannot run concurrently.
Stopped/incomplete captures, resets, reconnects, changed configurations or
30 seconds without fresh measurements require a new capture. Applying twice from
the same result is rejected. Boot IDs are not available, so restart a recording
after any board reboot; USB connection epochs and observed clock resets provide
guards, not global device identities.

Before applying, the host saves intent, exact input samples, parameters, method,
and old/proposed settings to `data/sessions/calibrations/<capture-id>.json`, then
records the outcome and actual readback. This happens even without manual
recording. An `applying` audit after a host crash means **unconfirmed**, not success.
During recording, `radio.ndjson` and sample `radio_revision` retain the change.
Historical measurements are never overwritten; `raw_m` remains the timestamp
result under the hardware settings effective for that sample.

Other sources retain the browser-local suggestion-only assistant; demo/replay
cannot apply to boards or validate physical calibration.

## Tests

Run `make verify` from the repository root. See the [development guide](../README.md#change-and-check-code)
for Python, Node.js, and native-C prerequisites.
