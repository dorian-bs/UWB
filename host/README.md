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

### Live radio settings (two USB-connected boards)

Both boards need the updated STM32 firmware, with the initiator built as `TxDebug`
and the responder as `RxDebug`. Connect each board's USB CDC port to the host.
Keep the actual device paths in ignored `LAB_NOTES.md`; the variables below stand
for those local paths:

```bash
uwb-lab --source dual-serial --serial-port "$UWB_TX_USB" --responder-port "$UWB_RX_USB"
```

The **Radio / sampling** panel reads each board's identity and current settings.
It enables **Apply to both boards** only when both USB boards have been identified.
Controls are unavailable in demo, replay, TCP, and single-board serial modes.
These change physical radio settings, not a synthetic noise model:

- Requested ranging rate: 0.1-10 Hz, rounded to a 100-10000 ms poll period.
  Successful sample rate is measured separately from recent host arrivals.
  This limit is a command guard, **not a verified hardware throughput claim**.
- Channel: 5 or 9; preamble: 64, 128, 256, 512, or 1024 symbols.
- Data rate: 850 kbps or 6.8 Mbps.
- TX power: hexadecimal DW3000 register value, applied to both boards.
  It is **not dBm**; use only values approved for your boards and lab/regulatory setup.

See the [radio and sampling parameter reference](../docs/EXPERIMENTS_AND_VALIDATION.md#radio-and-sampling-parameter-reference)
for each setting's physical meaning, supported values, expected effects on bias/spread
and reception, dependent firmware settings, plot interpretation, and a comparison protocol.

Applying settings pauses both radios, configures and reads back both, then resumes
the responder before the initiator. A missing acknowledgement or mismatch triggers
rollback. If rollback cannot be confirmed, reachable boards are left paused, with
individual state/errors shown; disconnected board state is unknown, not assumed safe.
Reachable boards are restored independently where pause/readback can be confirmed;
they do not resume if the complete pair cannot be confirmed. Control acknowledgements
are independent of slow graph clients; discarded USB input lines are counted in the panel.
After restoring connectivity, inspect the states and apply a compatible configuration
to recover. **Load current TX settings** fills the form without changing either board.
Settings are volatile and return to firmware defaults after a board restart.
The UI never automatically applies saved hardware settings.

Graphs keep earlier samples. Recordings include `radio.ndjson` with settings snapshots
and apply/rollback outcomes; range records carry `radio_revision` so earlier measurements
are not relabelled. A board's revision is local to its boot, not a globally unique ID.
Record restarts as separate sessions until boot IDs are available.

Host tests use isolated pseudo-terminals and native C tests, not physical boards.
Build/flash and on-hardware rate/RF validation must still be performed explicitly.

Recorded session:

```bash
uwb-lab --source replay --replay-file data/sessions/SESSION/telemetry.ndjson
```

Recordings are written under `data/sessions/` and are intentionally ignored by Git.

## Repeated experiments and noise analysis

1. Start a source and apply a distance or position prior in **Geometry prior**.
2. In **Experiment setup**, choose a name, run length in seconds, integer run count,
   and interval between runs. The current prior and available radio state are
   snapshotted when **Start batch** is pressed.
3. Inspect run/interval progress. The host owns the schedule: refreshing or closing
   the browser does not stop it. **Stop batch** preserves completed runs and marks
   an active run incomplete. Detected source disconnection or host shutdown also
   interrupts the batch; it does not automatically resume.
4. Choose analysis methods in **Analysis setup** and a dataset/scope below the
   signal plots. All runs of the selected batch are pooled by default; the
   current run or one saved run can still be selected.
   **Recompute full closed dataset** saves a reproducible full-data result.

Prior edits, radio changes, and manual recording are unavailable throughout a
batch, including its intervals. A run with no valid measurements fails the batch.
Consecutive automatic recordings do not imply independent physical experiments.

The methods are residual statistics, density histogram with Gaussian baseline,
pooled spectrum (Welch PSD or Lomb-Scargle power), and a seeded 1-6-component GMM.
Autocorrelation is removed for now.
If an already-open dashboard reports rejected `acf` or `max_lag` inputs after an
update, hard-refresh it (`Cmd+Shift+R` on macOS, `Ctrl+Shift+R` elsewhere).
Old tabs keep executing their loaded JavaScript until refreshed; reloading does
not stop a batch. Existing saved analysis preferences migrate away from these
retired settings. Dashboard HTML, styles and scripts are served with
`Cache-Control: no-store` to prevent stale assets on subsequent page loads.
Choose either raw or corrected range minus the **recorded** expected distance.
These residuals include prior/geometry errors; mixture components are not
automatically LOS/NLOS classes. No outlier trimming or correction is applied.
The default demo varies its true distance, so its residuals are not a stationary
sensor-noise reference.

Diagnostics use every eligible sample recorded so far in the selected runs,
including the open run, and refresh through one coalescing background worker.
There is no 2000-sample analysis cap. Each job freezes the recording lengths at
submission so later arrivals do not change that job's population or source hash.
Residual statistics are limited to sample count, bias, sample standard deviation,
RMSE and P95 absolute error. The existing signal display window and its
20-sample rolling **population** spread are unchanged; analysis sample spread
uses `ddof=1`.

The histogram and its Gaussian baseline fit individual residual samples, never
run averages. The GMM also fits those individual residuals directly (not the
histogram bars), using expectation-maximization for the selected component count.
Its weights are the fitted proportions, its means are residual offsets from the
recorded prior, and its spreads are within-component standard deviations.
The displayed component curves are weighted densities; their sum is the mixture.
The component count is user-selected, not automatically chosen by AIC/BIC;
those scores describe the fit and help compare models on the same data.

Spectra use the full sample arrays, not run means. With consecutive sequences
and device-time deltas within 5% of both run and pooled medians, the method
remains Welch: spectra are computed within each run and their densities pooled
by eligible sample count on a common grid. Segment length is at most 256
samples, limited by the shortest run; Hann windows overlap by half. Segments
never bridge pauses or run boundaries; their means are removed.

Missing samples, irregular timestamps or differing cadences select
**Lomb-Scargle** for the whole selection instead of rejecting it. Each run
is mean-subtracted and analysed at its actual recorded device timestamps;
results are pooled by sample count. All observed eligible samples contribute:
there is no interpolation, synthetic gap filling or concatenation of pauses.
The panel labels this as **power (m^2), not PSD density (m^2/Hz)**; its vertical
scale is not directly comparable to Welch. The unnormalized Lomb-Scargle
periodogram is scaled by `2/N` per run before sample-weighted pooling.
Missing sequence counts are displayed. The common frequency grid begins at
one cycle over the shortest run span, ends below half the slowest run's median
cadence, and is oversampled approximately fivefold, with 256-4096 grid points.
Grid spacing is not a claim of physical frequency resolution.

Each nonempty run still needs at least 32 eligible samples, increasing device
times and no sequence resets/duplicates. An invalid clock, reset, insufficient
data or no common frequency band produces an explicit reason. Frequency comes
from device timing, not the requested rate. At roughly 1 Hz, allow at least
32 seconds and enough successful measurements per run for a spectrum.

Distances/errors are displayed to 0.001 m; fractional readouts use three decimal
places and sample counts remain exact integers. Tiny non-length values such as
spectral power use scientific notation instead of being rounded to zero.
This is display rounding, not a claim of millimetre accuracy: recorded data and
calculations are not rounded.

Residual statistics use a prominent signed-bias tile with separate standard
deviation, RMSE and P95 tiles. Short labels retain their full names in hover
titles. Values and their smaller units stay together on one baseline in both
analysis tiles and live readouts; tiles reflow rather than splitting numbers
or wrapping units underneath. Sample/run counts appear once above the cards, alongside the residual
source and nonzero exclusions. **Details** there contains provenance and prior
semantics. Spectrum cards identify the estimator and relevant metrics without
repeating the shared counts or a single-curve legend.
Card **Details** holds method/scaling notes and secondary GMM fit diagnostics;
the expansion state survives live refreshes. Real failures remain visible.
Multi-curve plots retain short legends, and the signal plot footer shows only
the displayed/requested sample count (buffer/rolling-window details on hover).
Existing cards remain visible while the selection updates,
including run transitions. If a method is temporarily unavailable (for example
the new run is too short for PSD), its last successful result remains with an
explicit previous-source notice until a replacement is ready.

### Antenna-delay calibration assistant

Use the assistant only on a fixed-distance **LOS** recording with a stable setup.
It estimates a correction from the **corrected** range mean versus the configured
expected distance, while also showing the corresponding `raw_m` reference so you
can confirm the systematic offset source.

Recommendations are **suggestions only**. The host/dashboard never writes antenna
delays to boards automatically. Apply changes manually in STM32 firmware, then
rebuild/reflash both boards and record a new validation run.

Each batch is portable:

```text
data/sessions/experiments/<batch-id>/
  manifest.json
  intervals.ndjson
  runs/<session-id>/{manifest.json,telemetry.ndjson,priors.ndjson,radio.ndjson}
  analyses/<result-id>.json
```

All parsed range measurements, including invalid and inter-run measurements,
are retained independently of signal-display limits. Interval samples are excluded
from ordinary run analysis. This is not a lossless capture of debug bytes or
measurements lost before parsing; existing health counters still matter.
Unfinished batches reopen as interrupted after a process crash. Saved analyses
retain source hashes, run states, prior references, parameters, seed and
numerical-library versions without rewriting telemetry.

The approved [design](../docs/superpowers/specs/2026-10-17-repeated-experiments-and-noise-analysis-design.md)
and [implementation plan](../docs/superpowers/plans/2026-10-17-repeated-experiments-and-noise-analysis.md)
describe the contracts. Moving priors, changing conditions between runs, and
classification/model evaluation remain future work.

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