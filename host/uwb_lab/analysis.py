from __future__ import annotations

import math
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy import signal, stats
from sklearn.exceptions import ConvergenceWarning
from sklearn.mixture import GaussianMixture

METHODS = {
    "statistics": {"label": "Residual statistics", "parameters": []},
    "distribution": {
        "label": "Histogram + distribution fit",
        "parameters": ["fit", "histogram_bins", "components", "seed"],
    },
    "psd": {"label": "Spectrum / PSD (all runs)", "parameters": []},
}

FITS = {
    "gaussian": "Gaussian",
    "gmm": "Gaussian mixture",
    "lognormal": "Log-normal (shifted)",
    "student_t": "Student-t",
    "laplace": "Laplace",
    "skew_normal": "Skew-normal",
}

SpectrumRun = tuple[str, NDArray[np.float64], NDArray[np.int64], NDArray[np.int64]]


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    batch_id: str
    scope: Literal["current", "run", "batch"] = "batch"
    run_id: str | None = None
    run_from: int | None = Field(default=None, ge=1, strict=True)
    run_to: int | None = Field(default=None, ge=1, strict=True)
    exclude_runs: list[str] = Field(default_factory=list, max_length=10_000)
    mode: Literal["cumulative", "full"] = "cumulative"
    field: Literal["raw_m", "corrected_m"] = "corrected_m"
    methods: list[Literal["statistics", "distribution", "psd"]] = Field(default_factory=lambda: list(METHODS))
    fit: Literal["auto", "gaussian", "gmm", "lognormal", "student_t", "laplace", "skew_normal"] = "gaussian"
    histogram_bins: int = Field(default=30, ge=1, le=200, strict=True)
    components: int = Field(default=2, ge=1, le=6, strict=True)
    seed: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)
    compare_show_fits: bool = False

    @model_validator(mode="after")
    def check_scope(self) -> AnalysisRequest:
        if (self.scope == "run") != (self.run_id is not None):
            raise ValueError("run_id is required exactly when scope is run")
        if self.run_from and self.run_to and self.run_from > self.run_to:
            raise ValueError("run_from must not exceed run_to")
        return self


class AnalysisSample(BaseModel):
    seq: int
    time_ms: int
    board: int
    peer: int
    raw_m: float
    corrected_m: float
    expected_m: float | None = None
    prior_revision: int | None = None
    valid: bool = Field(strict=True)


class ResultModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class Metric(ResultModel):
    key: str
    label: str
    value: float | int | None
    unit: str = ""


class Series(ResultModel):
    label: str
    x: list[float]
    y: list[float]
    style: Literal["line", "bars", "stems", "points", "band"] = "line"
    # "band" fills between y and y2; colour, opacity and legend visibility are presentation hints.
    y2: list[float] | None = None
    color: str | None = None
    alpha: float | None = None
    dashed: bool = False
    width: float | None = None
    legend: bool = True

    @model_validator(mode="after")
    def check_lengths(self) -> Series:
        if len(self.x) != len(self.y) or (self.y2 is not None and len(self.y2) != len(self.y)):
            raise ValueError("Series coordinates must have equal lengths")
        return self


class Annotation(ResultModel):
    x: float
    y: float
    text: str


class Heatmap(ResultModel):
    """z[row][column] sampled at (x[column], y[row]); markers are x positions of run boundaries."""

    x: list[float]
    y: list[float]
    z: list[list[float]]
    z_label: str
    z_min: float
    z_max: float
    markers: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_shape(self) -> Heatmap:
        if len(self.z) != len(self.y) or any(len(row) != len(self.x) for row in self.z):
            raise ValueError("Heatmap values must be rows x columns")
        return self


class Plot(ResultModel):
    x_label: str
    y_label: str
    series: list[Series]
    title: str = ""
    x_scale: Literal["linear", "log"] = "linear"
    y_scale: Literal["linear", "log"] = "linear"
    annotations: list[Annotation] = Field(default_factory=list)
    heatmap: Heatmap | None = None


class Table(ResultModel):
    title: str = ""
    columns: list[str]
    rows: list[list[str | float | int | None]]

    @model_validator(mode="after")
    def check_width(self) -> Table:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Table rows must match the columns")
        return self


class AnalysisResult(ResultModel):
    status: Literal["ok", "unavailable", "failed"] = "ok"
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    plots: list[Plot] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_reason(self) -> AnalysisResult:
        if self.status != "ok" and not self.reason:
            raise ValueError("Unavailable or failed results require a reason")
        return self


@dataclass
class Moments:
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    absolute: float = 0.0
    squared: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        self.absolute += abs(value)
        self.squared += value * value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def summary(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "bias_m": self.mean if self.count else None,
            "std_m": math.sqrt(max(0, self.m2) / (self.count - 1)) if self.count > 1 else None,
            "mae_m": self.absolute / self.count if self.count else None,
            "rmse_m": math.sqrt(self.squared / self.count) if self.count else None,
            "min_m": self.minimum,
            "max_m": self.maximum,
        }


def describe(values: NDArray[np.float64]) -> AnalysisResult:
    if not len(values):
        return AnalysisResult(status="unavailable", reason="No eligible residuals")
    moments = Moments()
    for value in values:
        moments.add(float(value))
    summary = moments.summary()
    labels = {
        "count": "Eligible samples",
        "bias_m": "Bias",
        "std_m": "Sample standard deviation",
        "rmse_m": "RMSE",
    }
    metrics = [
        Metric(key=key, label=label, value=summary[key], unit="" if key == "count" else "m")
        for key, label in labels.items()
    ]
    metrics.append(
        Metric(
            key="p95_abs_m",
            label="P95 absolute error",
            value=float(np.quantile(np.abs(values), 0.95)),
            unit="m",
        )
    )
    return AnalysisResult(metrics=metrics)


@dataclass
class Candidate:
    label: str
    parameters: list[tuple[str, float]]
    log_likelihood: float
    pdf: Callable[[NDArray[np.float64]], NDArray[np.float64]]
    cdf: Callable[[NDArray[np.float64]], NDArray[np.float64]]
    components: list[list[float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def k(self) -> int:
        return len(self.parameters)


SCIPY_FITS = {
    "gaussian": (stats.norm, ["Mean (m)", "Spread (m)"]),
    "lognormal": (stats.lognorm, ["Shape", "Shift (m)", "Scale (m)"]),
    "student_t": (stats.t, ["Degrees of freedom", "Location (m)", "Scale (m)"]),
    "laplace": (stats.laplace, ["Location (m)", "Scale (m)"]),
    "skew_normal": (stats.skewnorm, ["Shape", "Location (m)", "Scale (m)"]),
}


def _scipy_candidate(name: str, values: NDArray[np.float64]) -> Candidate | None:
    dist, names = SCIPY_FITS[name]
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        if name == "lognormal":
            spread = float(np.std(values))
            params = dist.fit(values, 1.0, loc=float(values.min()) - spread, scale=spread)
        else:
            params = dist.fit(values)
        log_likelihood = float(np.sum(dist.logpdf(values, *params)))
    if not np.isfinite(log_likelihood):
        return None
    return Candidate(
        label=FITS[name],
        parameters=[(label, float(value)) for label, value in zip(names, params, strict=True)],
        log_likelihood=log_likelihood,
        pdf=lambda grid: dist.pdf(grid, *params),
        cdf=lambda grid: dist.cdf(grid, *params),
    )


def _gmm_candidate(values: NDArray[np.float64], components: int, seed: int) -> Candidate | str:
    if len(values) < max(10, 5 * components) or len(np.unique(values)) < max(2, components):
        return "Too few samples or distinct values for this mixture"
    model = GaussianMixture(
        n_components=components,
        covariance_type="full",
        random_state=seed,
        n_init=5,
        reg_covar=1e-10,
        max_iter=500,
    )
    matrix = values.reshape(-1, 1)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        model.fit(matrix)
    if not model.converged_:
        return "Mixture did not converge"
    order = np.argsort(model.means_.ravel())
    rows = [
        [
            float(model.weights_[i]),
            float(model.means_.ravel()[i]),
            float(np.sqrt(model.covariances_.ravel()[i])),
        ]
        for i in order
    ]

    def pdf(grid: NDArray[np.float64]) -> NDArray[np.float64]:
        return sum(w * stats.norm.pdf(grid, m, s) for w, m, s in rows)

    def cdf(grid: NDArray[np.float64]) -> NDArray[np.float64]:
        return sum(w * stats.norm.cdf(grid, m, s) for w, m, s in rows)

    return Candidate(
        label=f"{FITS['gmm']} ({components})",
        parameters=[
            (f"{name} {i + 1}", row[j])
            for i, row in enumerate(rows)
            for j, name in enumerate(("w", "m", "s"))
        ],
        log_likelihood=float(model.score(matrix)) * len(values),
        pdf=pdf,
        cdf=cdf,
        components=rows,
        notes=["Components are exploratory, not LOS/NLOS labels; covariance regularization = 1e-10 m^2"]
        + [str(item.message) for item in caught],
    )


def distribution_fit(
    values: NDArray[np.float64], bins: int, fit: str, components: int, seed: int
) -> AnalysisResult:
    if not len(values):
        return AnalysisResult(status="unavailable", reason="No eligible residuals")
    density, edges = np.histogram(values, bins=bins, density=True)
    bars = Series(
        label="Empirical density",
        x=((edges[:-1] + edges[1:]) / 2).tolist(),
        y=density.tolist(),
        style="bars",
    )
    plot_labels = {"x_label": "Residual (m)", "y_label": "Density (1/m)"}
    if float(np.std(values)) == 0:
        return AnalysisResult(
            warnings=["Constant residuals: no distribution can be fitted"],
            plots=[Plot(series=[bars], **plot_labels)],
        )
    if len(values) < 10 or len(np.unique(values)) < 2:
        return AnalysisResult(status="unavailable", reason="Too few samples or distinct values for a fit")

    candidates: dict[str, Candidate] = {}
    skipped: list[str] = []
    for name in FITS:
        if name == "gmm":
            outcome = _gmm_candidate(values, components, seed)
            if isinstance(outcome, str):
                skipped.append(f"{FITS[name]}: {outcome}")
            else:
                candidates[name] = outcome
        else:
            outcome = _scipy_candidate(name, values)
            if outcome is None:
                skipped.append(f"{FITS[name]}: fit failed")
            else:
                candidates[name] = outcome
    count = len(values)
    scored = {}
    for name, candidate in candidates.items():
        aic = 2 * candidate.k - 2 * candidate.log_likelihood
        bic = candidate.k * math.log(count) - 2 * candidate.log_likelihood
        ks = float(stats.kstest(values, candidate.cdf).statistic)
        scored[name] = (candidate, aic, bic, ks)

    notes = []
    if fit == "auto":
        if not scored:
            return AnalysisResult(status="failed", reason="No distribution fit converged")
        fit = min(scored, key=lambda name: scored[name][1])
        notes.append(f"Auto fit selected {FITS[fit]} (lowest AIC).")
    if fit not in scored:
        reason = next((note for note in skipped if note.startswith(FITS[fit])), f"{FITS[fit]}: fit failed")
        return AnalysisResult(status="failed", reason=reason)

    mean, std = float(np.mean(values)), float(np.std(values))
    grid = np.linspace(min(edges[0], mean - 4 * std), max(edges[-1], mean + 4 * std), 300)
    chosen, aic, bic, ks = scored[fit]
    series = [bars, Series(label=chosen.label, x=grid.tolist(), y=chosen.pdf(grid).tolist())]
    if chosen.components:
        for index, (weight, mu, sigma) in enumerate(chosen.components, start=1):
            curve = weight * stats.norm.pdf(grid, mu, sigma)
            series.append(Series(label=f"Component {index}", x=grid.tolist(), y=curve.tolist()))

    comparison = sorted(
        (
            [candidate.label, aic_value, bic_value, ks_value]
            for candidate, aic_value, bic_value, ks_value in scored.values()
        ),
        key=lambda row: row[1],
    )
    parameter_table = (
        Table(title="Components", columns=["Weight", "Mean (m)", "Spread (m)"], rows=chosen.components)
        if chosen.components
        else Table(
            title="Parameters",
            columns=["Parameter", "Value"],
            rows=[[name, value] for name, value in chosen.parameters],
        )
    )
    return AnalysisResult(
        warnings=notes + chosen.notes + skipped,
        metrics=[
            Metric(key="log_likelihood", label="Mean log likelihood", value=chosen.log_likelihood / count),
            Metric(key="aic", label="AIC", value=aic),
            Metric(key="bic", label="BIC", value=bic),
            Metric(key="ks", label="KS distance", value=ks),
        ],
        tables=[
            parameter_table,
            Table(title="Compare models", columns=["Model", "AIC", "BIC", "KS distance"], rows=comparison),
        ],
        plots=[Plot(series=series, **plot_labels)],
    )


SPECTRUM_SEGMENT = 256
SPECTRUM_MIN_SAMPLES = 32
# A window whose strongest line is 12 dB above its own median is far outside what white noise produces.
NARROWBAND_RATIO = 16.0
MAX_RUN_LINES = 12
MAX_SPECTROGRAM_COLUMNS = 240
ACF_LAGS = 20


def power_spectrum(
    values: NDArray[np.float64], time_ms: NDArray[np.int64], seq: NDArray[np.int64]
) -> AnalysisResult:
    return pooled_power_spectrum([("Run", values, time_ms, seq)])


def pooled_power_spectrum(runs: list[SpectrumRun]) -> AnalysisResult:
    eligible = [run for run in runs if len(run[1]) >= SPECTRUM_MIN_SAMPLES]
    if not eligible:
        return AnalysisResult(
            status="unavailable", reason=f"PSD needs at least {SPECTRUM_MIN_SAMPLES} eligible samples per run"
        )
    # The window length follows the longest run only. A run that is still recording therefore cannot
    # change the frequency grid; it joins the estimate once it holds one full window.
    longest = max(len(run[1]) for run in eligible)
    segment = min(SPECTRUM_SEGMENT, max(SPECTRUM_MIN_SAMPLES, longest // 2))
    usable = [run for run in eligible if len(run[1]) >= segment]
    result = _pooled_spectrum(usable, segment)
    if result.status == "ok":
        kept = {run[0] for run in usable}
        left_out = [run[0] for run in runs if run[0] not in kept]
        if left_out:
            result.warnings.insert(
                0,
                f"Left out runs with fewer than {segment} eligible samples (shorter than one window): "
                + ", ".join(left_out),
            )
    return result


def _regular_windows(values: NDArray[np.float64], fs: float, segment: int):
    frequencies, centers, power = signal.spectrogram(
        values,
        fs=fs,
        window="hann",
        nperseg=segment,
        noverlap=segment // 2,
        detrend="constant",
        scaling="density",
        mode="psd",
    )
    return frequencies, centers, power.T


def _gapped_windows(values: NDArray[np.float64], times: NDArray[np.int64], fs: float, segment: int):
    """Hann-tapered Lomb-Scargle windows on actual timestamps, scaled like a one-sided PSD density."""
    seconds = (times - times[0]).astype(np.float64) / 1000
    width = segment / fs
    frequencies = np.arange(segment // 2 + 1) * fs / segment
    angular = 2 * np.pi * frequencies[1:]
    rows: list[NDArray[np.float64]] = []
    centers: list[float] = []
    start = 0.0
    while start + width <= seconds[-1] + 1e-9:
        low, high = np.searchsorted(seconds, [start, start + width], side="left")
        count = high - low
        if count >= 0.6 * segment:
            offsets = seconds[low:high] - start
            taper = 0.5 * (1 - np.cos(2 * np.pi * offsets / width))
            tapered = (values[low:high] - np.mean(values[low:high])) * taper
            with np.errstate(divide="ignore", invalid="ignore"):
                power = signal.lombscargle(offsets, tapered, angular, normalize=False)
            density = np.nan_to_num(2 * power * count / (fs * np.sum(taper**2)), nan=0.0, posinf=0.0)
            rows.append(np.concatenate(([0.0], density)))
            centers.append(start + width / 2)
        start += width / 2
    if not rows:
        return frequencies, np.empty(0), np.empty((0, len(frequencies)))
    return frequencies, np.array(centers), np.array(rows)


def _autocorrelation(
    series: list[tuple[NDArray[np.float64], NDArray[np.int64]]], max_lag: int
) -> tuple[list[float | None], float]:
    """Sample autocorrelation using only pairs exactly `lag` sequence numbers apart, so gaps never bias it."""
    numerator = np.zeros(max_lag + 1)
    pairs = np.zeros(max_lag + 1)
    energy, count = 0.0, 0
    for values, seq in series:
        centered = values - np.mean(values)
        energy += float(centered @ centered)
        count += len(centered)
        for lag in range(1, max_lag + 1):
            index = np.searchsorted(seq, seq + lag)
            safe = np.minimum(index, len(seq) - 1)
            found = (index < len(seq)) & (seq[safe] == seq + lag)
            numerator[lag] += float(np.sum(centered[found] * centered[safe[found]]))
            pairs[lag] += int(found.sum())
    variance = energy / count if count else 0.0
    rho = [
        float(numerator[lag] / pairs[lag] / variance) if pairs[lag] >= 10 and variance > 0 else None
        for lag in range(1, max_lag + 1)
    ]
    return rho, 1.96 / math.sqrt(count) if count else math.nan


def _run_label(run_id: str) -> str:
    match = re.search(r"(?:^|-)run-(\d+)$", run_id)
    return f"#{int(match.group(1))}" if match else run_id


def _spectrogram_heatmap(
    windows: list[tuple[NDArray[np.float64], NDArray[np.float64], float]], frequencies: NDArray[np.float64]
) -> Heatmap | None:
    """Window spectra from every run laid end to end in time, in dB over the global median level."""
    columns = [rows for _, rows, _ in windows if len(rows)]
    if sum(len(rows) for rows in columns) < 2:
        return None
    power = np.vstack(columns)[:, 1:]
    level = float(np.median(power))
    if level <= 0:
        return None
    times: list[float] = []
    markers: list[float] = []
    offset = 0.0
    for centers, rows, duration in windows:
        if offset and len(rows):
            markers.append(offset)
        times.extend((centers + offset).tolist())
        offset += duration
    group = math.ceil(len(power) / MAX_SPECTROGRAM_COLUMNS)
    merged = [power[start : start + group].mean(axis=0) for start in range(0, len(power), group)]
    merged_times = [float(np.mean(times[start : start + group])) for start in range(0, len(times), group)]
    decibels = 10 * np.log10(np.maximum(np.array(merged), level * 1e-6) / level)
    low, high = np.percentile(decibels, [1, 99.7])
    return Heatmap(
        x=merged_times,
        y=frequencies[1:].tolist(),
        z=np.round(decibels.T, 1).tolist(),
        z_label="dB over median level",
        z_min=float(low),
        z_max=float(max(high, low + 3)),
        markers=markers,
    )


def _pooled_spectrum(runs: list[SpectrumRun], segment: int) -> AnalysisResult:
    deltas = []
    regular = True
    for run_id, _values, time_ms, seq in runs:
        reason = None
        delta = np.diff(time_ms.astype(float))
        median = float(np.median(delta))
        if np.any(delta <= 0):
            reason = "Device times are not strictly increasing"
        elif np.any(np.diff(seq) <= 0):
            reason = "Sequence reset or duplicate: analyse separate uninterrupted recordings"
        if reason:
            return AnalysisResult(status="unavailable", reason=f"{run_id}: {reason}")
        if np.any(np.diff(seq) != 1) or np.any(np.abs(delta - median) > 0.05 * median):
            regular = False
        deltas.append(delta)
    all_deltas = np.concatenate(deltas)
    period = float(np.median(all_deltas))
    if np.any(np.abs(all_deltas - period) > 0.05 * period):
        regular = False
    fs = 1000 / period
    frequencies = np.arange(segment // 2 + 1) * fs / segment

    spectra = []
    for run_id, values, times, seq in runs:
        if regular:
            _, centers, rows = _regular_windows(values, fs, segment)
        else:
            _, centers, rows = _gapped_windows(values, times, fs, segment)
        if len(rows):
            spectra.append((run_id, values, times, seq, centers, rows))
    if not spectra:
        return AnalysisResult(
            status="unavailable", reason="Selected runs hold no complete window with enough samples"
        )
    count = sum(len(values) for _, values, *_ in spectra)
    window_count = sum(len(rows) for *_, rows in spectra)
    run_density = [rows.mean(axis=0) for *_, rows in spectra]
    density = sum(
        mean * (len(values) / count) for mean, (_, values, *_rest) in zip(run_density, spectra, strict=True)
    )
    body = density[1:]
    total = float(np.sum(body))
    floor = float(np.median(body))
    peak = 1 + int(np.argmax(body))
    peak_hz = float(frequencies[peak])
    peak_db = 10 * math.log10(density[peak] / floor) if floor > 0 else None
    tonal = float(np.sum(density[max(1, peak - 2) : peak + 3]) / total * 100) if total > 0 else None
    flatness = float(np.exp(np.mean(np.log(body))) / np.mean(body)) if np.all(body > 0) else None

    every_window = np.vstack([rows for *_, rows in spectra])[:, 1:]
    medians = np.median(every_window, axis=1)
    strongest = np.max(every_window, axis=1)
    tonal_windows = (medians > 0) & (strongest > NARROWBAND_RATIO * medians)
    narrowband = float(np.mean(tonal_windows) * 100)
    wander = (
        float(np.std(frequencies[1 + np.argmax(every_window[tonal_windows], axis=1)]))
        if np.sum(tonal_windows) >= 3
        else None
    )

    max_lag = min(ACF_LAGS, count // 4)
    rho, limit = _autocorrelation([(values, seq) for _, values, _, seq, *_ in spectra], max_lag)
    lag_one = rho[0] if rho else None
    lag_two = rho[1] if len(rho) > 1 else None

    dof = 1.89 * window_count  # 50% overlapped Hann windows are slightly correlated
    low_band = density[1:] * dof / stats.chi2.ppf(0.975, dof)
    high_band = density[1:] * dof / stats.chi2.ppf(0.025, dof)
    estimator = "Pooled Welch PSD" if regular else "Pooled Lomb-Scargle spectrum"
    series = [
        Series(label=estimator, x=frequencies.tolist(), y=density.tolist(), color="#4fb3d1", width=2.4),
        Series(
            label="95% confidence",
            x=frequencies[1:].tolist(),
            y=low_band.tolist(),
            y2=high_band.tolist(),
            style="band",
            color="#4fb3d1",
            alpha=0.3,
        ),
    ]
    if 2 <= len(spectra) <= MAX_RUN_LINES:
        for (run_id, *_), mean in zip(spectra, run_density, strict=True):
            series.append(
                Series(
                    label=f"Run {_run_label(run_id)}",
                    x=frequencies[1:].tolist(),
                    y=mean[1:].tolist(),
                    color="#8a9893",
                    alpha=0.3,
                    width=1,
                    legend=False,
                )
            )
    annotations = []
    if floor > 0:
        series.append(
            Series(
                label="Noise floor (median)",
                x=[float(frequencies[1]), float(frequencies[-1])],
                y=[floor, floor],
                color="#e0a73a",
                dashed=True,
            )
        )
        if peak_db is not None and peak_db >= 6:
            annotations.append(
                Annotation(x=peak_hz, y=float(density[peak]), text=f"{peak_hz:.2f} Hz  +{peak_db:.0f} dB")
            )
    plots = [
        Plot(
            title="Power spectral density",
            x_label="Frequency (Hz)",
            y_label="PSD (m^2/Hz)",
            series=series,
            x_scale="log",
            y_scale="log",
            annotations=annotations,
        )
    ]
    offsets_s = [(times[-1] - times[0]) / 1000 for _, _, times, *_ in spectra]
    heatmap = _spectrogram_heatmap(
        [(centers, rows, duration) for (*_, centers, rows), duration in zip(spectra, offsets_s, strict=True)],
        frequencies,
    )
    if heatmap is not None:
        plots.append(
            Plot(
                title="Spectrogram",
                x_label="Recorded time (s)",
                y_label="Frequency (Hz)",
                series=[],
                heatmap=heatmap,
            )
        )
    if any(value is not None for value in rho):
        lags = [float(lag) for lag in range(1, max_lag + 1)]
        plots.append(
            Plot(
                title="Autocorrelation",
                x_label="Lag (samples)",
                y_label="Autocorrelation",
                series=[
                    Series(
                        label="Autocorrelation",
                        x=lags,
                        y=[value if value is not None else 0.0 for value in rho],
                        style="stems",
                        color="#3ecf9f",
                    ),
                    Series(
                        label="95% white-noise limit",
                        x=lags,
                        y=[limit] * len(lags),
                        dashed=True,
                        color="#e0a73a",
                    ),
                    Series(
                        label="Lower limit",
                        x=lags,
                        y=[-limit] * len(lags),
                        dashed=True,
                        color="#e0a73a",
                        legend=False,
                    ),
                ],
            )
        )

    tables = []
    if len(spectra) >= 2:
        rows = []
        for (run_id, values, *_), mean in zip(spectra, run_density, strict=True):
            run_peak = 1 + int(np.argmax(mean[1:]))
            run_floor = float(np.median(mean[1:]))
            rows.append(
                [
                    _run_label(run_id),
                    float(frequencies[run_peak]),
                    10 * math.log10(mean[run_peak] / run_floor) if run_floor > 0 else None,
                    len(values),
                ]
            )
        tables.append(
            Table(
                title="Dominant tone per run",
                columns=["Run", "Peak (Hz)", "Above floor (dB)", "Samples"],
                rows=rows,
            )
        )
    if any(value is not None for value in rho):
        tables.append(
            Table(
                title="Whiteness check",
                columns=["Lag", "Autocorrelation", "95% limit", "Verdict"],
                rows=[
                    [lag, value, limit, "correlated" if abs(value) > limit else "within noise"]
                    for lag, value in enumerate(rho[:5], start=1)
                    if value is not None
                ],
            )
        )

    missing = sum(int(np.sum(np.diff(seq) - 1)) for _, _, _, seq, *_ in spectra)
    notes = [
        f"Hann windows of {segment} samples with 50% overlap, never crossing run boundaries; runs are "
        "weighted by sample count. Each window is demeaned. The window length follows the longest "
        "run, so a run still recording joins the estimate only once it holds a full window and the "
        "frequency grid never changes while data arrive."
    ]
    if not regular:
        notes.append(
            "Gapped or jittered timing: every window uses the actual device timestamps "
            "(Lomb-Scargle on Hann-tapered samples), scaled like a one-sided PSD, with no "
            "interpolation or gap compression."
        )
    metrics = [
        Metric(key="peak_hz", label="Dominant tone", value=peak_hz, unit="Hz"),
        Metric(key="peak_excess_db", label="Tone above floor", value=peak_db, unit="dB"),
        Metric(key="tonal_share_pct", label="Power in tone", value=tonal, unit="%"),
        Metric(key="narrowband_pct", label="Windows with a tone", value=narrowband, unit="%"),
        Metric(key="tone_wander_hz", label="Tone wander (std)", value=wander, unit="Hz"),
        Metric(key="flatness", label="Spectral flatness", value=flatness),
        Metric(key="acf_lag1", label="Lag-1 autocorr.", value=lag_one),
        Metric(key="acf_lag2", label="Lag-2 autocorr.", value=lag_two),
        Metric(key="count", label="Eligible samples", value=count),
        Metric(key="runs", label="Runs", value=len(spectra)),
        Metric(key="fs_hz", label="Device-timed sample rate", value=fs, unit="Hz"),
        Metric(key="segment", label="Window", value=segment, unit="samples"),
        Metric(key="resolution_hz", label="Frequency resolution", value=fs / segment, unit="Hz"),
        Metric(key="windows", label="Averaged windows", value=window_count),
    ]
    if not regular:
        metrics.append(Metric(key="missing_samples", label="Missing sequence numbers", value=missing))
    return AnalysisResult(warnings=notes, metrics=metrics, plots=plots, tables=tables)
