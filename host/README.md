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
   signal plots. Select the current run, one saved run, or all runs pooled.
   **Recompute full closed dataset** saves a reproducible full-data result.

Prior edits, radio changes, and manual recording are unavailable throughout a
batch, including its intervals. A run with no valid measurements fails the batch.
Consecutive automatic recordings do not imply independent physical experiments.

The initial methods are residual statistics, density histogram with Gaussian
baseline, sample-lag autocorrelation, Welch PSD, and a seeded 1-6-component GMM.
Choose either raw or corrected range minus the **recorded** expected distance.
These residuals include prior/geometry errors; mixture components are not
automatically LOS/NLOS classes. No outlier trimming or correction is applied.
The default demo varies its true distance, so its residuals are not a stationary
sensor-noise reference.

Live diagnostics use at most 2000 eligible samples and refresh through one
coalescing background worker. Full-selection counts, bias, sample spread,
MAE/RMSE and bounds are labelled separately from window quantiles/fits. The
existing signal display window and its 20-sample rolling **population** spread
are unchanged; analysis sample spread uses `ddof=1`.

PSD requires at least 32 valid samples, consecutive sequences, increasing device
times, and all timing deltas within 5% of their median. Frequency comes from
device timing, not the requested rate. Ineligible data shows a reason instead
of interpolation or an unjustified FFT. ACF and PSD stay separate for each run,
even when distribution fitting pools the runs. At roughly 1 Hz, allow at least
32 seconds and enough successful measurements for a spectrum.

Each batch is portable:

```text
data/sessions/experiments/<batch-id>/
  manifest.json
  intervals.ndjson
  runs/<session-id>/{manifest.json,telemetry.ndjson,priors.ndjson,radio.ndjson}
  analyses/<result-id>.json
```

All parsed range measurements, including invalid and inter-run measurements,
are retained independently of live-window limits. Interval samples are excluded
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