# Repeated Experiments and Noise Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` for inline execution, or `subagent-driven-development` if the user selects delegated execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Schedule identical timed experiment runs, archive all parsed measurements, and provide selectable live and saved-data noise diagnostics.

**Architecture:** Extend the existing service with a host-owned batch runner, a pure numerical analysis module, and one bounded analysis worker. Reuse the existing session recorder and prior validation. Add independent experiment/analysis UI modules without changing the existing signal plots or firmware.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, NumPy, SciPy, scikit-learn, existing plain JavaScript/canvas, pytest and Node's built-in test runner.

**Approved specification:** [Design](../specs/2026-10-17-repeated-experiments-and-noise-analysis-design.md).

**Execution policy:** No Git commits. Keep changes in the current worktree unless the user requests isolation. Run failing tests before implementation and focused passing tests after each change. Commands below are planned validation, not results already obtained.

## Execution record

Implemented inline on `master`, as explicitly requested, without commits.
`make verify` passes Ruff checks/formatting and all 61 pytest tests, including
the Node UI controller regressions, native firmware checks, and HTTP smoke tests.
The existing Starlette/httpx deprecation warning remains unrelated to this work.

Browser acceptance covered two completed runs and an interval; full pooled
analysis with separate temporal cards; raw/corrected selection; method toggles;
390-pixel layout without page overflow; reopening data after service restart;
and completing both runs after navigating the dashboard away.

The independent code review identified two lifecycle bugs. Regression tests now
verify that failed session finalization marks the run failed, and that delayed
partial analysis cannot remain displayed for an explicitly selected closed run.
Both fixes pass the final verification. Temporary demo processes/data were
removed. Hardware and firmware were not built, flashed, or exercised.

---

## Repository facts and integration constraints

- `Runtime.ingest()` in `host/uwb_lab/app.py` currently parses, records, then broadcasts.
- `LabState.samples` retains 300 samples; browser history retains 2000. Neither is an archival dataset.
- `LabState.set_prior()` resets its rolling residual window. Preserve this behavior.
- `SessionRecorder.start(name, source)` and `stop()` have existing callers/tests.
- `RadioControl.recorder` retains the original recorder reference. Do not replace
  `Runtime.recorder` to change recording directories.
- USB control acknowledgements are dispatched independently of telemetry. Preserve
  that ordering, queue bounds, cancellation handling, and radio transaction locks.
- The dual-USB supervisor polls every two seconds. A disconnect must be latched at
  the board connection transition, not inferred only by polling the dashboard.
- Replay currently reconstructs compact telemetry and discards stored expected
  values. Saved analysis must read archived samples directly, not replay them
  through the latest global prior.
- The default demo contains a changing distance. It demonstrates the workspace;
  stationary/noise-model acceptance tests need their own known synthetic datasets.
- Tests share the module-level app runtime. New API tests must replace and restore
  it with a temporary-root instance rather than relying on test order.

## Files and responsibilities

| File | Change |
| --- | --- |
| `host/pyproject.toml` | Numerical dependencies |
| `host/uwb_lab/core.py` | Backward-compatible recorder destination, atomic manifests, incomplete finalization |
| `host/uwb_lab/experiments.py` | Batch input/state, scheduling, archive ownership, interval retention, batch discovery |
| `host/uwb_lab/analysis.py` | Typed numerical inputs/results, parameter catalogue, pure methods |
| `host/uwb_lab/analysis_jobs.py` | Bounded live snapshots, full-file loading, one worker, provenance and saved results |
| `host/uwb_lab/app.py` | Lifecycle, source transitions, ownership guards, HTTP endpoints and asset routes |
| `host/uwb_lab/radio.py` | Optional disconnect notification only; no protocol/control behavior changes |
| `host/uwb_lab/static/experiments.js` | Experiment setup/progress and control locking |
| `host/uwb_lab/static/analysis.js` | Analysis selection, polling, reusable result cards/rendering |
| `host/uwb_lab/static/index.html` | New panels, progress, dataset/scope controls and card container |
| `host/uwb_lab/static/app.css` | Related responsive styles |
| `host/uwb_lab/static/app.js` | Small integration hooks into the existing request/status/display lifecycle |
| `host/tests/test_core.py` | Recorder regression/failure tests |
| `host/tests/test_experiments.py` | Clock-controlled batch lifecycle and archive tests |
| `host/tests/test_analysis.py` | Numerical acceptance and edge cases |
| `host/tests/test_analysis_jobs.py` | Worker bounds, provenance, reload and stale results |
| `host/tests/test_experiment_api.py` | Fresh-runtime integration/ownership/source tests |
| `host/tests/test_radio.py` | USB disconnect callback regression |
| `host/tests/experiment_analysis.test.cjs` | Pure UI state/result tests |
| `host/tests/test_display.py` | Run the additional Node tests through pytest |
| `host/README.md` | User workflow and limitations |
| `docs/ARCHITECTURE.md` | Batch/API/result/storage contracts |
| `docs/EXPERIMENTS_AND_VALIDATION.md` | Method definitions and statistical cautions |
| `docs/IMPLEMENTATION_STATUS.md` | Delivered features and remaining limits |

Do not create additional service layers, a database, a plugin loader, a frontend
framework, or firmware changes.

## Task 1: Pin numerical contracts and dependencies

**Files:** `host/pyproject.toml`, `host/uwb_lab/analysis.py`, `host/tests/test_analysis.py`.

- [ ] Add a failing input-validation/serialization test:

```python
import json

import pytest
from pydantic import ValidationError

from uwb_lab.analysis import AnalysisRequest, AnalysisResult


def test_analysis_request_and_empty_result_are_explicit():
    request = AnalysisRequest(batch_id="batch-a", scope="run", run_id="run-1")
    assert request.field == "corrected_m"
    assert request.window_size == 2000
    assert request.components == 2
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", scope="run")
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", components=7)
    result = AnalysisResult(status="unavailable", reason="No valid samples")
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert result.reason == "No valid samples"
```

- [ ] Run `.venv/bin/python -m pytest host/tests/test_analysis.py -q`;
  expect an import failure before adding the module.
- [ ] Add `numpy>=1.26,<3`, `scipy>=1.11,<2`, and
  `scikit-learn>=1.4,<2` to project dependencies. Install only after this
  manifest change with `.venv/bin/python -m pip install -e './host[dev]'`.
- [ ] Implement Pydantic request/result contracts. Reject extra request fields,
  non-finite numbers, invalid method IDs, and inconsistent scope/run selection.
  Define these exact initial request fields:

```python
class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    batch_id: str
    scope: Literal["current", "run", "batch"] = "current"
    run_id: str | None = None
    mode: Literal["live", "full"] = "live"
    field: Literal["raw_m", "corrected_m"] = "corrected_m"
    methods: list[Literal["statistics", "histogram", "acf", "psd", "gmm"]] = Field(
        default_factory=lambda: ["statistics", "histogram", "acf", "psd", "gmm"]
    )
    window_size: int = Field(default=2000, ge=1, le=2000)
    histogram_bins: int = Field(default=30, ge=1)
    max_lag: int = Field(default=30, ge=1)
    components: int = Field(default=2, ge=1, le=6)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
```

  Add an after-validator requiring `run_id` exactly when `scope == "run"`.
  Full mode requires closed selected runs; enforce that using archive state in
  Task 7, not by assuming an arbitrary batch ID is closed.

  Result models:

  - `Metric(key, label, value: float | int | None, unit)`.
  - `Series(label, x: list[float], y: list[float], style)` where style is
    `line`, `bars`, `stems`, or `points`; validate equal vector lengths.
  - `Plot(x_label, y_label, series: list[Series])`.
  - `Table(columns: list[str], rows: list[list[str | float | int | None]])`;
    validate row widths.
  - `AnalysisResult(status, reason, warnings, metrics, plots, tables)`,
    with statuses `ok`, `unavailable`, and `failed`; collection defaults use
    `default_factory`. Unavailable/failed states require a reason.
  - `AnalysisSample`: validated stored board/peer/sequence/device time,
    raw/corrected values, validity, expected value, prior revision, host time,
    and optional radio revision. Accept additive archive fields.

- [ ] Add a static `METHODS` catalogue of IDs, labels and parameter metadata.
  Method functions are defined in Tasks 5-6 and wired directly, not dynamically loaded.
- [ ] Rerun the test; expect PASS. Check the new module with Ruff.

## Task 2: Make session recording safe for experiment ownership

**Files:** `host/uwb_lab/core.py`, `host/tests/test_core.py`.

- [ ] Add tests for a custom parent directory, unique starts in the same second,
  incomplete finalization, partial-open failure, and failed manifest replacement.
  Preserve the existing successful manual-recording test unchanged.

```python
def test_incomplete_run_uses_requested_parent(tmp_path):
    recorder = SessionRecorder(tmp_path)
    run = recorder.start("run-1", "demo", parent=tmp_path / "experiments" / "batch-a" / "runs")
    recorder.stop(completed=False, reason="Acquisition disconnected")
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["completed"] is False
    assert manifest["reason"] == "Acquisition disconnected"
    assert not recorder.active
```

- [ ] Run `.venv/bin/python -m pytest host/tests/test_core.py -q`;
  expect the new signature/behavior assertions to fail.
- [ ] Add keyword-only `parent: Path | None = None` to the existing
  `start(self, name: str, source: str) -> Path`. Add keyword-only
  `completed: bool = True` and `reason: str | None = None` to the existing
  `stop(self) -> Path`. Existing calls retain their defaults.
  Use `parent` only as an internal destination, not a user-provided API path.
  Append a short UUID to generated session identities so subsecond runs cannot
  collide. Do not change required telemetry fields.

  Implement the atomic JSON helper in `core.py` and reuse it for batch manifests
  and saved results:

```python
def write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
```

  Use `ExitStack` during startup so a failed second/third file open closes
  earlier handles. Assign recorder fields only after initialization succeeds.
  On finalization, attempt each handle close and manifest update, retain
  failure reasons, clear ownership only after cleanup, and raise storage errors.
  Never force `completed=True` after an incomplete or failed outcome.
- [ ] Use Pylance signature-compatibility checks for `start`/`stop`, then run
  `.venv/bin/python -m pytest host/tests/test_core.py host/tests/test_app.py host/tests/test_radio.py -q`.
  Expect existing callers and new tests to pass.

## Task 3: Implement deterministic batch state and archival scheduling

**Files:** `host/uwb_lab/experiments.py`, `host/tests/test_experiments.py`.

- [ ] Introduce a fake monotonic clock in tests and pin the runner contract:

```python
class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_run_and_interval_deadlines(tmp_path):
    clock = Clock()
    state = LabState()
    prior = state.set_prior({"mode": "distance", "distance_m": 2.0})
    recorder = SessionRecorder(tmp_path)
    runner = BatchRunner(tmp_path, recorder, clock=clock, health_snapshot=lambda: {})
    runner.start(
        BatchInput(name="LOS", duration_s=10, run_count=2, interval_s=3),
        source="demo",
        prior=prior,
        radio_snapshot=None,
    )
    assert runner.status()["phase"] == "recording"
    runner.record_sample({
        "type": "range", "v": 1, "board": 1, "peer": 2, "seq": 1,
        "time_ms": 1000, "raw_m": 2.01, "corrected_m": 2.01, "valid": True,
        "host_time": "2026-10-17T10:00:01+00:00",
        "expected_m": 2.0, "prior_revision": prior.revision,
    })
    clock.now = 10
    runner.advance()
    assert runner.status()["phase"] == "interval"
    clock.now = 13
    runner.advance()
    assert runner.status()["run_index"] == 2
    runner.stop("stopped", "Test cleanup")
```

  Add a separate test that deliberately omits the sample and verifies the
  zero-valid-measurement failure policy.
- [ ] Run `.venv/bin/python -m pytest host/tests/test_experiments.py -q`;
  expect import/contract failures.
- [ ] Implement `BatchInput` with strict integer `run_count`, positive finite
  `duration_s`, nonnegative finite `interval_s`, bounded name/notes following
  existing recording/prior input conventions, and forbidden extra fields.
- [ ] Implement `BatchRunner` with the following concrete operations:

  - `start(payload, *, source, prior, radio_snapshot)`: reserve
    ownership; create a UUID batch directory; snapshot inputs; open the first
    run using the shared recorder; save prior/radio/health snapshots; start its
    deadline. Take final health snapshots from the injected callback when each
    run ends; report counter resets rather than negative deltas.
  - `advance()`: if the current deadline has expired, finalize the run or
    interval and transition once. Compute each new deadline after the new phase
    actually opens; do not catch up by manufacturing empty runs.
  - `record_sample(sample)`: advance before classifying the sample; write
    recording-phase samples through the shared recorder and interval samples
    to `intervals.ndjson` with batch/phase/run-boundary identity.
  - `stop(outcome, reason)`: idempotently terminate a batch; preserve completed
    runs, mark the active run incomplete/failed, mark unexecuted runs, close
    interval storage and atomically persist the terminal manifest.
  - `request_stop(outcome, reason)`: latch a stop request without file I/O or
    exceptions. Process it before the next sample classification or supervisor
    deadline check, even if the source has already reconnected.
  - `status()`: return public IDs, phase/outcome, run progress/deadline and
    errors, never machine-specific paths.
  - `list_batches()` and `load_batch(batch_id)`: load only validated IDs beneath
    the experiment root; return portable metadata and explicit corruption errors.

  A recording-phase end with zero eligible samples fails the batch. Do not
  silently complete an empty run. Use `make_prior()`/the actual `Prior` object,
  not duplicate distance/position validation.
- [ ] Add an `async supervise()` loop that calls `advance()` on a short bounded
  timer while active and exits after a terminal transition. Its clock is the
  injected monotonic callable; tests drive `advance()` directly without sleeping.
- [ ] Add tests for zero intervals, partial finalization/storage errors,
  interval data retention/exclusion, prior snapshots, completion counts, repeated
  stop, interrupted/reopened manifests, and more than 2000 archived samples.
- [ ] Run the focused experiment/core tests; expect PASS.

## Task 4: Wire ownership, connection loss and batch APIs end-to-end

**Files:** `host/uwb_lab/app.py`, `host/uwb_lab/radio.py`,
`host/tests/test_experiment_api.py`, `host/tests/test_radio.py`.

- [ ] Create fresh-runtime API tests using `monkeypatch` and temporary storage.
  Patch the module-level runtime only within each fixture and stop all created
  acquisition/supervisor tasks in cleanup. Test the actual HTTP handlers:

```python
def test_batch_locks_prior_and_manual_recording(client):
    client.post("/api/prior", json={"mode": "distance", "distance_m": 2.0})
    started = client.post(
        "/api/experiments/start",
        json={"name": "LOS", "duration_s": 30, "run_count": 2, "interval_s": 5},
    )
    assert started.status_code == 200
    assert client.post("/api/prior", json={"distance_m": 3.0}).status_code == 409
    assert client.post("/api/recording/start", json={"name": "manual"}).status_code == 409
    assert client.post("/api/recording/stop").status_code == 409
    assert client.post("/api/experiments/stop").status_code == 200
```

  Define the `client` fixture in this test module: a fresh `Runtime`, demo
  source, a temporary `SessionRecorder`, its matching `BatchRunner`, and
  `TestClient(app)` inside a monkeypatch context. Do not reuse the existing
  smoke-test module's persistent data directory.
- [ ] Run the new API/radio tests and confirm the new behaviors fail.
- [ ] Allow `Runtime` an optional data-root constructor argument, preserving
  its default environment behavior. Construct its batch runner using the same
  recorder referenced by `RadioControl`.
- [ ] Add a single runtime ownership lock covering manual recording, prior
  changes, batch startup and radio apply. Return 409 when ownership/configuration
  is busy; do not queue a request until it unexpectedly becomes valid.
  Keep acquisition independent of this operation lock.
- [ ] Add synchronous connection-transition notification to the runtime and
  invoke it on every TCP, serial, replay, demo and acquisition-loop transition.
  A disconnect latches an interruption immediately even if reconnect follows
  before a browser poll.
- [ ] Extend `SerialBoard.run()` with keyword-only
  `on_disconnect: Callable[[str], None] | None = None`, leaving the existing
  ingest parameter and return type unchanged. Its transition hook is:

```python
if on_disconnect is not None:
    on_disconnect(self.error or f"{self.role}: USB reader disconnected")
```

  Invoke it after marking the board disconnected and before awaited cleanup.
  The callback only latches an interruption; it performs no file I/O and cannot
  raise into serial/ACK cleanup.
  Pass the runtime's interruption notification from dual-serial acquisition.
  Preserve the existing pending-ACK cleanup and write/rollback lock handling.
  Test that a responder disconnect interrupts a batch even without a new range.
- [ ] In `Runtime.ingest()`, route each parsed sample once: active batch owns
  recording/interval classification; otherwise use manual recording. A batch
  recording failure updates explicit batch/runtime errors without stopping
  ordinary sample delivery. Do not record a sample twice.
- [ ] On graceful shutdown, stop the batch as interrupted before generic manual
  recorder cleanup, then settle supervisor/analysis work and acquisition.
  Do not use the existing unconditional `recorder.stop()` path for an incomplete run.
- [ ] Implement:

| Endpoint | Behavior |
| --- | --- |
| `POST /api/experiments/start` | Validate input and ownership, snapshot prior/radio, start host runner |
| `POST /api/experiments/stop` | Stop only experiment ownership; return terminal batch state |
| `GET /api/experiments` | List saved batches, with unfinished ones visibly interrupted |
| `GET /api/experiments/{batch_id}` | Manifest, run metadata and comparison summaries |
| `GET /api/status` | Add experiment state; preserve existing manual status fields |

  Return 409 for state conflicts, 422 for typed input failures, 404 for missing
  IDs, and an explicit 500 plus logged details for storage failures.
- [ ] Test locking during intervals, missing prior/source, in-flight radio apply,
  all connection modes, shutdown, and manual recording outside a batch. Run
  the focused API/core/radio/experiment tests; expect PASS.

## Task 5: Implement residual preparation, moments and distribution diagnostics

**Files:** `host/uwb_lab/analysis.py`, `host/tests/test_analysis.py`.

- [ ] Add known-value tests with hand-computed residuals:

```python
def test_residual_summary_has_correct_sign_and_sample_spread():
    values = np.array([-0.01, 0.0, 0.02, 0.03])
    result = describe(values)
    metrics = {item.key: item.value for item in result.metrics}
    assert metrics["bias_m"] == pytest.approx(0.01)
    assert metrics["std_m"] == pytest.approx(np.std(values, ddof=1))
    assert metrics["rmse_m"] == pytest.approx(np.sqrt(np.mean(values**2)))
    assert metrics["mae_m"] == pytest.approx(0.015)
    assert metrics["p95_abs_m"] == pytest.approx(np.quantile(np.abs(values), 0.95))
```

- [ ] Confirm the test fails, then implement:

  - `prepare_samples(records, field)`: validate archive samples, preserve
    per-run identity/timing, compute `record[field] - record["expected_m"]`,
    and report invalid/missing-reference/non-finite exclusions separately.
  - `describe(values)`: full listed statistics; one-sample standard deviation
    unavailable; zero samples unavailable; quantiles use NumPy's linear method.
  - `Moments.add(value)`: Welford mean/M2 plus absolute/squared sums and bounds;
    maintain exact cheap full-run statistics independently of live-window eviction.
  - `histogram(values, bins)`: NumPy density histogram with a SciPy normal
    maximum-likelihood baseline. Constant data gets an explicit degenerate-fit
    warning and a meaningful empirical display, not an infinite Gaussian.
  - `autocorrelation(values, max_lag)`: demean, normalize by zero-lag energy,
    and return lags `0..min(max_lag, n-1)`. Label the estimator and sample-lag
    axis. Constant data is unavailable.

  The core ACF estimator is:

```python
centered = values - np.mean(values)
energy = float(np.dot(centered, centered))
if energy == 0:
    return AnalysisResult(status="unavailable", reason="Constant residuals")
lags = np.arange(min(max_lag, len(values) - 1) + 1)
correlation = np.array(
    [np.dot(centered[: len(values) - lag], centered[lag:]) / energy for lag in lags]
)
```

  Execute ACF separately per run. Emit gap/irregular-cadence warnings instead
  of claiming that sample lags measure elapsed seconds.
- [ ] Test raw/corrected selection, changing recorded prior revisions, invalid
  records, old records lacking a prior, units, Gaussian baseline normalization,
  constants, one sample, empty data, and finite JSON serialization.
- [ ] Run `.venv/bin/python -m pytest host/tests/test_analysis.py -q`; expect PASS.

## Task 6: Implement timing-qualified PSD and reproducible GMM

**Files:** `host/uwb_lab/analysis.py`, `host/tests/test_analysis.py`.

- [ ] Add a known-tone PSD test and a seeded, well-separated mixture test:

```python
def test_psd_uses_device_timing_not_requested_rate():
    fs = 10.0
    time_ms = np.arange(1000, dtype=np.int64) * 100
    values = np.sin(2 * np.pi * 1.25 * time_ms / 1000)
    frequencies, density = welch_density(values, time_ms, np.arange(1000))
    peak = frequencies[np.argmax(density)]
    assert peak == pytest.approx(1.25, abs=fs / 256)


def test_gmm_recovers_known_components():
    rng = np.random.default_rng(123)
    values = np.concatenate([rng.normal(-0.04, 0.005, 1500), rng.normal(0.06, 0.008, 500)])
    result = fit_gmm(values, components=2, seed=0)
    assert result.status == "ok"
    table = result.tables[0]
    means = np.array([row[1] for row in table.rows], dtype=float)
    weights = np.array([row[0] for row in table.rows], dtype=float)
    np.testing.assert_allclose(means, [-0.04, 0.06], atol=0.003)
    np.testing.assert_allclose(weights, [0.75, 0.25], atol=0.03)
```

- [ ] Run and observe failure before implementation.
- [ ] Implement `welch_density(values, time_ms, seq)` and its result wrapper:
  at least 32 points; finite increasing device times; consecutive sequence;
  every delta within 5% of its median. On ineligibility return a documented
  unavailable result with the precise timing reason.

```python
delta_ms = np.diff(time_ms.astype(float))
median_ms = float(np.median(delta_ms))
eligible = (
    len(values) >= 32
    and np.all(delta_ms > 0)
    and np.all(np.diff(seq) == 1)
    and np.all(np.abs(delta_ms - median_ms) <= 0.05 * median_ms)
)
if not eligible:
    raise ValueError("PSD requires consecutive, sufficiently regular device timing")
nperseg = min(256, len(values))
frequencies, density = scipy.signal.welch(
    values,
    fs=1000 / median_ms,
    window="hann",
    nperseg=nperseg,
    noverlap=nperseg // 2,
    detrend="constant",
    scaling="density",
)
```

  Guard short inputs before taking the median. The public wrapper translates
  this specific validation failure to an unavailable result, not a silent
  empty series. Expose `fs`, segment length and frequency resolution.
- [ ] Implement `fit_gmm(values, components, seed)` using:

```python
model = GaussianMixture(
    n_components=components,
    covariance_type="full",
    random_state=seed,
    n_init=5,
    reg_covar=1e-10,
    max_iter=500,
)
```

  Require at least `max(10, 5 * components)` observations and at least
  `max(2, components)` distinct values. Capture and expose convergence warnings,
  verify `converged_`, sort components by mean, and report weights/means/spreads,
  log likelihood, AIC and BIC. Use a common density grid for weighted component
  curves and their sum. Clearly label covariance regularization in square metres.
- [ ] Test exact 5% PSD tolerance boundaries, non-increasing times, gaps,
  insufficient data, constant mixtures, non-convergence, component-sum density,
  repeatability under the saved seed, and per-run temporal isolation.
- [ ] Run focused numerical tests and Ruff; expect PASS.

## Task 7: Add bounded analysis jobs, saved loading and provenance

**Files:** `host/uwb_lab/analysis_jobs.py`, `host/tests/test_analysis_jobs.py`,
`host/uwb_lab/app.py`.

- [ ] Add tests with an injected blocking analysis callable controlled by
  `threading.Event`: submit A, then B and C while A runs; assert maximum
  concurrency one, B superseded, and C the only pending job. Test shutdown
  while the blocking callable is still running; it must settle without a
  second concurrent computation.
- [ ] Confirm failures, then implement `AnalysisService`:

  - Bounded current-run and pooled-batch eligible-sample deques, each at most
    2000 samples, fed once from batch ingest. Do not retain a 2000-sample deque
    for every completed run. Store finalized per-run moments/summaries with
    run metadata; load selected completed-run windows from files into a
    bounded cache.
  - Immutable snapshots passed to numerical code; never share a mutable deque
    with the worker.
  - One active worker and one replaceable pending request; explicit superseded
    job state, a small bounded recent-job/result cache, no unbounded executor queue.
  - Monotonic throttling for automatic live work: no more than one refresh every
    two seconds, with the same bounded scheduler covering GMM.
  - Unique job ID, full recipe, and `recipe_key` on every response. Compute the
    key as SHA-256 of canonical sorted request JSON plus resolved run IDs;
    capture current-run identity before starting work. The client matches job
    ID and recipe key before publishing results.
  - Full mode reads each selected closed session directly from its
    `telemetry.ndjson`; interval records are not part of run selections.
  - Live current-run statistics merge full-run moments with separately labelled
    window-only median/quantiles/fits. Full mode computes all metrics from files.
  - Pooled descriptive fits use combined eligible observations; ACF/PSD remain
    labelled per-run results with individual unavailable warnings.
  - Full-result persistence uses the atomic helper and includes source SHA-256,
    run IDs, prior references, complete request, analysis version, package versions,
    actual counts, mode/window and creation time.

  Loading corrupt JSON or a missing run is an explicit failed job. Catch only
  anticipated validation/numerical/I/O failures, log their context, and preserve
  their reasons. Never convert every exception into an empty successful card.
- [ ] Add endpoints:

| Endpoint | Contract |
| --- | --- |
| `GET /api/analysis/methods` | Static catalogue and initial parameter definitions |
| `POST /api/analysis/jobs` | Validate selection; return 202 and job ID/state/recipe |
| `GET /api/analysis/jobs/{job_id}` | queued/running/succeeded/unavailable/failed/superseded plus result |
| `GET /api/experiments/{batch_id}/analyses` | List saved full-data artifacts |

  Missing/expired jobs return explicit 404. Live results do not write a new
  artifact every refresh. Full-data recomputation is explicit.
- [ ] Test restart/reload, saved provenance, identical live/full inputs, raw
  immutability, old prior values, invalid exclusions, over-2000-sample archival
  analysis, bounded queues/cache, thread failures and stale-selection results.
- [ ] Run the job/numerical/API tests; expect PASS.

## Task 8: Implement experiment controls without changing signal display behavior

**Files:** `host/uwb_lab/static/experiments.js`, `host/uwb_lab/static/index.html`,
`host/uwb_lab/static/app.js`, `host/uwb_lab/static/app.css`,
`host/tests/experiment_analysis.test.cjs`, `host/tests/test_display.py`,
`host/uwb_lab/app.py`.

- [ ] Add Node tests for pure payload/state helpers:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const { experimentPayload, experimentLocked } = require("../uwb_lab/static/experiments.js");

test("experiment duration and interval are independent", () => {
  assert.deepEqual(experimentPayload({
    name: "LOS", notes: "", duration_s: "10", run_count: "3", interval_s: "2",
  }), { name: "LOS", notes: "", duration_s: 10, run_count: 3, interval_s: 2 });
});

test("both acquisition phases retain ownership", () => {
  assert.equal(experimentLocked({ phase: "recording", active: true }), true);
  assert.equal(experimentLocked({ phase: "interval", active: true }), true);
  assert.equal(experimentLocked({ phase: "idle", active: false }), false);
});
```

- [ ] Run `node --test host/tests/experiment_analysis.test.cjs`; expect missing-module failure.
- [ ] Implement/export `experimentPayload()` and `experimentLocked()` following
  the existing CommonJS/browser guard pattern. `experimentPayload()` rejects
  blank/non-finite durations, fractional counts, and negative intervals with
  visible errors rather than converting blanks to zero.
- [ ] Add the experiment form, a read-only snapshot/summary of the existing
  prior, progress strip, interval/run state and stop button. Preserve the
  independent collapsed radio/prior panels and their existing IDs.
- [ ] Add `startExperimentPanel({request, showError, refreshStatus})`, returning
  an `update(status)` hook. Call that hook from the existing status refresh.
  Use the existing request helper; do not add a second status poller.
- [ ] Disable manual recording, prior submit and radio apply while the batch
  owns acquisition, including intervals. Keep stop batch available. Re-evaluate
  radio availability on every status refresh so the existing radio updater
  cannot accidentally re-enable a batch-locked control.
- [ ] Serve the new script with an explicit asset route and load it before
  `app.js`. Extend `test_display.py` to run both existing and new `.test.cjs`
  files in one Node invocation. Preserve existing signal preferences and
  pause-view behavior.
- [ ] Run the Node/display/API tests; expect PASS.

## Task 9: Implement reusable analysis cards and saved-data selection

**Files:** `host/uwb_lab/static/analysis.js`, `host/uwb_lab/static/index.html`,
`host/uwb_lab/static/app.js`, `host/uwb_lab/static/app.css`,
`host/tests/experiment_analysis.test.cjs`, `host/uwb_lab/app.py`.

- [ ] Add tests for independent card visibility, source/recipe matching, finite
  series bounds, histogram/stem/line styles, and error/result state rendering.
  Pin stale-result protection:

```javascript
const { resultMatches } = require("../uwb_lab/static/analysis.js");

test("an old run result cannot replace a new selection", () => {
  assert.equal(resultMatches(
    { jobId: "job-b", recipeKey: "run-2:corrected" },
    { job_id: "job-a", recipe_key: "run-1:corrected" }
  ), false);
  assert.equal(resultMatches(
    { jobId: "job-b", recipeKey: "run-2:corrected" },
    { job_id: "job-b", recipe_key: "run-2:corrected" }
  ), true);
});
```

- [ ] Confirm failure; implement/export `resultMatches()`, result-shape validation
  and chart-bound helpers independently of DOM access.
- [ ] Add `startAnalysisPanel({request, showError})` returning `update(status)`.
  Load catalogue/batches, maintain live/current versus saved selection explicitly,
  submit throttled jobs, and poll only the outstanding matching job. A saved
  selection must not jump back to the live run when a new run starts.
  A live current-run transition immediately invalidates the previous job
  selection, before the next refresh is submitted.
- [ ] Implement metric, table and plot renderers using safe DOM text operations.
  The chart renderer supports line, bars, stems and points, multiple curves,
  numeric axes and units, finite bounds, constant-series padding and resize.
  Keep it separate from the existing signal plot renderer; the two have
  different axes/history semantics.
- [ ] Show dataset/run IDs, raw/corrected choice, sample/exclusion counts,
  window/full-data mode, provisional/completed state and warnings on cards.
  Show GMM convergence and component table, not just a density curve.
  Show explicit unavailable reasons for PSD/ACF/GMM; do not hide failed cards.
- [ ] Add method checkboxes, parameter inputs, live-window setting, saved batch
  and run/scope selectors, full-data recompute and the per-run comparison table.
  Persist display/method preferences under a separate versioned key; never
  silently reuse preferences as hardware/prior commands.
- [ ] Add responsive styles matching the existing theme and sidebar, with
  stacked panels on narrow screens and overflow handling for result tables.
  Serve/load the new script before `app.js`.
- [ ] Run Node/display and relevant API tests; expect PASS.

## Task 10: Verify the complete workflow and update related documentation

**Files:** all changed files, `host/README.md`, `docs/ARCHITECTURE.md`,
`docs/EXPERIMENTS_AND_VALIDATION.md`, `docs/IMPLEMENTATION_STATUS.md`.

- [ ] Add an integration test that sets a fixed prior, records two short
  synthetic runs plus an interval, reopens the saved batch in a fresh runtime,
  computes each method from saved data, and verifies archive counts exceed
  live-window counts without cross-run PSD/ACF pairs.
- [ ] Add an interruption integration test: disconnect during run two, confirm
  run one remains complete, run two is incomplete, later runs are not executed,
  and results retain interruption provenance after reload.
- [ ] Run focused tests, then `make verify`. Expected: Ruff check/format and
  all existing/new pytest tests pass, including Node and real HTTP smoke tests.
  Do not claim success from the plan itself.
- [ ] Start the demo in an attached background session on an unused localhost
  port with a temporary data root outside real recordings:

```bash
UWB_SOURCE=demo UWB_DATA_DIR=/tmp/uwb-experiment-acceptance \
  .venv/bin/python -m uvicorn uwb_lab.app:app --host 127.0.0.1 --port 8017
```

  Resolve an unused temporary directory and port before execution; do not
  overwrite another user's directory or use a real hardware session root.
  Verify the process responds with `curl --fail http://127.0.0.1:8017/api/status`.
- [ ] Browser-check setup/start/interval/stop, panel locking, saved batch reload,
  independent analysis toggles and parameters, raw/corrected selection,
  insufficient-data warnings, full-data computation and per-run comparison.
  Check desktop and 390-pixel layouts for horizontal overflow. Confirm closing
  and reopening the page does not stop the batch.
- [ ] Document timing/PSD thresholds, full-run versus window metrics, sample
  versus rolling population spread, GMM limitations, raw preservation,
  automated-run dependence, interval exclusion, connection-loss behavior,
  archival layout, API contracts and numerical provenance.
- [ ] Update implementation status only with features verified usable. Keep
  hardware/RF validation and moving priors explicitly unimplemented.
- [ ] Stop only the demo process started for this task and remove only its
  explicitly resolved temporary recordings. Leave real sessions and unrelated
  changes alone. Summarize checks, limitations and changed files; do not commit.

## Plan self-review / coverage map

| Approved requirement | Tasks |
| --- | --- |
| Timed automatic repetition and intervals | 3-4, 8 |
| Fixed revisioned distance/position prior | 3-4, 8 |
| Portable complete runs and interval retention | 2-3, 10 |
| Storage/disconnect/shutdown/incomplete outcomes | 2-4, 10 |
| Recorder/radio/prior ownership and browser independence | 3-4, 8, 10 |
| Statistical/Gaussian/ACF/PSD/GMM outputs | 1, 5-6 |
| Live bounds, full-run moments, full saved recomputation | 5, 7, 9 |
| Timing qualification and no cross-run temporal pairs | 5-7, 10 |
| Configurable methods and reusable result shapes | 1, 8-9 |
| Provenance, saved reload and immutable telemetry | 2-3, 7, 10 |
| Existing UI/manual behavior and responsive layout | 4, 8-10 |
| Synthetic numerical, scheduler, API and UI tests | 1-10 |
| Related documentation and no firmware build/flash | 10 |

No task authorizes commits, physical board commands, live radio changes, or
modification of recorded raw measurements.
