# Repeated experiments and noise analysis

Date: 2026-10-17

Status: design sections and written specification approved by the user.
Implemented in the current workspace; see the [host workflow](../../../host/README.md#repeated-experiments-and-noise-analysis).
No Git commits were created.

## Goal

Turn the existing UWB dashboard into a reproducible experiment workspace:
configure repeated timed recordings, retain their measurements, inspect noise
diagnostics live, and reopen saved data for full-data analysis.

The first version supports identical configured conditions, not changing geometry
or automatic physical resets. A residual relative to an estimated prior is not
necessarily intrinsic sensor noise. Automatically separated recordings are not
necessarily statistically independent experiments.

## Approved decisions

- Extend the existing FastAPI service; scheduling and analysis belong on the host.
- Advance automatically, with a configurable interval between runs.
- Use one fixed distance or fixed 3D-position prior throughout a batch.
- Include statistics, histogram/Gaussian fit, autocorrelation, power spectral
  density, and configurable Gaussian mixture fitting in the first version.
- Stop on a detected acquisition disconnection; preserve an incomplete run.
- Keep experiment setup and analysis controls beside the plots in one workspace.
- Save this design and its implementation plan under `docs/superpowers/`.
  Do not create Git commits.

## Scope and non-goals

Reuse existing acquisition modes, priors, radio controls, manual recording, and
plain HTML/CSS/JavaScript. No firmware or telemetry-v1 change is required.

Not included: moving or multiple simultaneous priors, changing settings between
runs, automatic calibration/correction, LOS/NLOS classification, model-selection
claims on held-out data, firmware diagnostics, a notebook interface, a database,
a plugin framework, or a new frontend build system.

Future priors must remain possible without rewriting recorded measurements.
Preserve prior revisions and resolve expected distance per sample rather than
assuming every future analysis dataset has one globally constant expected value.

## Experiment setup

A batch definition contains:

- Name and notes.
- Positive finite run duration in seconds.
- Positive integer number of runs.
- Nonnegative finite interval duration in seconds; zero means back-to-back runs.
- An immutable snapshot of the existing prior, including its mode, expected
  distance, coordinates when applicable, uncertainty, note, and revision.
- Source mode and available radio-state/configuration snapshot.

Use the existing Geometry prior editor rather than implementing a second,
independently maintained prior-validation path. Show the selected prior in the
experiment setup summary. Require a configured prior before starting a batch.

Reject start when acquisition is disconnected, another batch or manual recording
owns the recorder, or a radio-setting transaction is in progress. Reject invalid
input explicitly without starting a partial batch.

The normal planned duration is:

```text
run_count * run_duration_s + (run_count - 1) * interval_s
```

There is no initial or final interval. File preparation/finalization can add
overhead; show actual recorded start/end times rather than pretending that the
planned duration is a measured duration.

## Scheduling and ownership

The server owns the schedule, independent of browsers and WebSocket connections.
Use monotonic deadlines for durations and UTC timestamps for archival boundaries.
Open and initialize a recording successfully before starting its run timer.

Run boundaries are host acquisition boundaries, not synchronized firmware
triggers. Preserve available device timing and host timestamps, and do not claim
that transport buffering provides exact physical sample-time alignment.

The lifecycle is:

```text
start -> recording run 1 -> interval -> recording run 2 -> ... -> completed
                           |
                    stop / disconnect / error
                           |
                     terminal outcome
```

Batch outcomes distinguish completed, operator-stopped, interrupted, and failed.
Per-run state distinguishes planned, recording, completed, incomplete, failed,
and not executed. State also identifies the active interval, run index,
elapsed/remaining time, and last error when applicable.

Lock prior changes and radio configuration changes for the entire active batch,
including intervals. Enforce ownership in the API, not only by disabling buttons.
Manual recording cannot start or stop an experiment-owned recording. Stop batch
is a separate action.

On stop, detected disconnection, or graceful host shutdown, finalize what can be
saved and mark the active run incomplete. Completed runs remain completed.
Unexecuted runs are identified as such. A run reaching its deadline with no valid
measurements is failed, and subsequent runs do not start.

Storage and finalization failures produce a visible failed state, never a
successful-looking completion. Preserve available files and the failure reason.
Do not silently restart, extend a timed run, or resume after reconnection.

After a process crash, unfinished manifests remain identifiable as interrupted
when reopened. Do not automatically resume them or relabel them complete.

## Archival layout

Use a portable, uniquely identified batch directory under the existing ignored
session-data root:

```text
experiments/<batch-id>/
  manifest.json
  intervals.ndjson
  runs/<session-id>/
    manifest.json
    telemetry.ndjson
    priors.ndjson
    radio.ndjson
  analyses/<result-id>.json
```

Each run uses the existing session format where possible. The batch manifest
links run IDs/session paths, planned settings, actual boundaries, outcomes,
sample counts, and available acquisition-health counter deltas. Use collision-safe
identities and relative paths. Publish manifest updates by atomic replacement.

Retain all parsed range samples, including invalid samples, with original raw
and corrected fields. Preserve prior revisions and available radio revisions.
Save samples acquired during intervals separately, with phase and timing
information; do not include them in ordinary run statistics or fits.

"All data" here means all parsed range measurements received during the batch,
including intervals, not a new lossless firmware/debug-byte transport. Existing
parser rejections and acquisition loss remain visible limitations and counters.
Neither live analysis bounds nor browser history may truncate archived telemetry.

Keep analysis artifacts separate from telemetry. Reopening a batch must work
after restarting the service; it cannot depend on retained in-memory samples.
Do not retrofit old sessions with invented prior or radio metadata.

## Workspace

Preserve the existing live measurement plots, display preferences, pause-view
semantics, and health information. Add:

- A batch progress strip with run/interval state and stop action.
- An experiment setup panel and prior summary.
- An analysis setup panel with method checkboxes and method-specific parameters.
- A dataset selector for the current batch or a saved batch.
- Scope selection: current run, one completed run, or pooled batch data.
- Reusable analysis cards and a per-run comparison table.

The first card set contains statistics, histogram with Gaussian baseline,
autocorrelation, PSD, and GMM. Cards can be shown or hidden independently.
Run comparison retains individual counts and summaries even when viewing pooled
data. Pooled distributions are sample-weighted and labelled accordingly.

Closing/collapsing panels does not alter acquisition, scheduling, or analysis
parameters. Pausing the existing view does not pause the experiment. Scope,
method, and analysis-window controls are independent of signal-display settings.

The preview created during design is illustrative, not an implementation or
measured result. Its analysis checkboxes demonstrate card visibility.

## Analysis inputs and computation

Run analysis in Python using the same functions for live and saved datasets.
NumPy/SciPy and scikit-learn are appropriate direct dependencies for the selected
methods; do not implement custom FFT, Welch, or EM algorithms.

Select either:

```text
raw_residual_m       = raw_m - recorded_expected_m
corrected_residual_m = corrected_m - recorded_expected_m
```

Default to corrected residuals, preserving existing residual sign. Require a
finite recorded expected value and a valid sample for statistical inclusion.
Keep excluded data in the archive and report exclusions by reason. Do not
silently trim outliers, impute values, demean the residual before reporting bias,
or subtract prior uncertainty from measured variance.

Live expensive diagnostics use an explicitly labelled bounded sample window,
independent of the signal display window. The initial maximum/default is the
latest 2000 eligible samples. Accumulate cheap full-run counts and first/second
moments throughout the run; distinguish these from window-only quantiles/fits.
Show the source scope and actual sample count on every result.

Throttle expensive live computation and run it outside the acquisition event
loop. Permit at most one active analysis computation per runtime, coalescing
superseded refresh requests instead of accumulating a job queue. Publish a result
only for its original dataset/configuration; never overwrite a new selection
with a stale result from a previous run or recipe.

Closed recordings support explicit full-data recomputation, independently of
browser and live-window limits. Report computation failures and insufficient
data rather than publishing fabricated values or zero-shaped fallbacks.

### Statistics

Include valid/excluded counts, observed duration/cadence, signed mean/bias,
sample standard deviation, median, MAE, RMSE, minimum/maximum residual, and
95th/99th percentiles of absolute error.

Label sample standard deviation explicitly; the existing rolling population
standard deviation keeps its existing semantics. With fewer than two samples,
sample standard deviation is unavailable, not zero. Statistics and units must be
consistent when displayed in metres or millimetres.

### Distribution and Gaussian baseline

Use a configurable histogram bin count and density normalization, with a fitted
single Gaussian and reported mean/spread. Handle constant datasets explicitly;
do not emit a zero-width Gaussian density or non-finite JSON.

### Autocorrelation

Report correlation against sample lag, not an invented time lag. Each run is
analysed independently. Invalid/missing samples and irregular cadence are
reported so sample lag cannot be mistaken for uniformly spaced elapsed time.
Constant or insufficient datasets have an explicit unavailable state.

At pooled scope, return per-run temporal results with run labels. Never form lag
pairs across run boundaries or interval samples.

### Spectrum / power spectral density

Use Welch PSD with constant detrending and a Hann window. Derive frequency from
recorded device-time deltas, not requested ranging rate or WebSocket arrivals.
Use density units of square metres per hertz.

Eligibility requires at least 32 eligible samples in one run, increasing device
times, consecutive sequence values, and time deltas within 5% of their median.
Use `nperseg = min(256, sample_count)` and half-window overlap. Disclose the
estimated sampling frequency, segment length, and frequency resolution.

If the selected live window or run fails timing eligibility, show why PSD is
unavailable. Do not silently interpolate, compress gaps, or concatenate runs.
At pooled scope, show separately eligible per-run PSDs and individual warnings.

### Gaussian mixture

Fit a one-dimensional Gaussian mixture with configurable component count
(initially 1-6, default 2), fixed random seed, and numerical covariance
regularization. Expose component weights, means, standard deviations,
convergence, AIC, and BIC; render weighted component densities and their sum.

Require enough finite observations and distinct values for the requested fit.
Explicitly report non-convergence, degeneracy, or insufficient data. Reorder
components by mean for stable presentation, without treating the order as a
semantic class label.

This is exploratory density fitting, not validation of an ML classifier or
evidence of multiple physical propagation regimes.

## Extensibility and provenance

Use a small typed method catalogue containing stable method IDs, labels,
parameter definitions/defaults, and callable analysis functions. Results contain
status/warnings, source provenance, scalar metrics, tables, and plotted series.
Support multiple curves and axis labels/units in the shared chart rendering.

Add a new method by adding its Python computation/parameters and composing
supported result shapes. Add a renderer only for a genuinely new visualization.
Avoid dynamic loading, dependency injection, or a generalized plugin API.

Saved results identify batch/run selection, included sample count and source
fingerprint, residual field, prior references, window/full-data mode, method
parameters, random seed, analysis version, numerical-library versions, and
creation time. Live artifacts are labelled provisional; saved full-data
recomputation uses a fixed dataset and configuration.

Preserve raw data when adding methods or new prior types. Saved result failures
or changing settings must not rewrite telemetry or reinterpret earlier samples
using the latest global prior.

## Verification

Add hardware-free tests using synthetic input and temporary recording roots:

- Deterministic clock-controlled duration/interval scheduling and run boundaries.
- Zero interval, unique identities, browser reconnect, and recorder ownership.
- API enforcement of prior/radio locks throughout runs and intervals.
- Stop, disconnect, shutdown, no valid samples, and storage/finalization failures.
- Saved-batch reload, incomplete recovery, interval retention/exclusion, and
  complete archival data beyond live-window size.
- Known statistical moments, percentile definitions, residual sign, invalid
  sample exclusion, constant inputs, and insufficient observations.
- Gaussian baseline and seeded mixture recovery with numerical tolerances,
  component-order independence, and explicit convergence failures.
- Known-tone PSD peak/frequency scaling, timing eligibility thresholds,
  irregular/reset/gapped timestamps, and no cross-run temporal concatenation.
- Live versus closed-file numerical consistency for the same samples/settings.
- Bounded/coalesced computation and rejection of stale result publication.
- Reusable-card visibility, preferences, scope changes, and existing display
  behavior through the repository's existing Node/pytest approach.

Run focused tests during development, then `make verify` and browser checks of
the demo/saved-data workflow and narrow-screen layout. Do not build or flash
firmware. Update the existing architecture, host guide, and implementation-status
documents when the feature becomes usable.
