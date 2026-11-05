from __future__ import annotations

import math
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
    fit: Literal["gaussian", "gmm", "lognormal", "student_t", "laplace", "skew_normal"] = "gaussian"
    histogram_bins: int = Field(default=30, ge=1, le=200, strict=True)
    components: int = Field(default=2, ge=1, le=6, strict=True)
    seed: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)

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
    style: Literal["line", "bars", "stems", "points"] = "line"

    @model_validator(mode="after")
    def check_lengths(self) -> Series:
        if len(self.x) != len(self.y):
            raise ValueError("Series coordinates must have equal lengths")
        return self


class Plot(ResultModel):
    x_label: str
    y_label: str
    series: list[Series]


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
    if fit not in candidates:
        reason = next((note for note in skipped if note.startswith(FITS[fit])), f"{FITS[fit]}: fit failed")
        return AnalysisResult(status="failed", reason=reason)

    count = len(values)
    mean, std = float(np.mean(values)), float(np.std(values))
    grid = np.linspace(min(edges[0], mean - 4 * std), max(edges[-1], mean + 4 * std), 300)
    chosen = candidates[fit]
    series = [bars, Series(label=chosen.label, x=grid.tolist(), y=chosen.pdf(grid).tolist())]
    if chosen.components:
        for index, (weight, mu, sigma) in enumerate(chosen.components, start=1):
            curve = weight * stats.norm.pdf(grid, mu, sigma)
            series.append(Series(label=f"Component {index}", x=grid.tolist(), y=curve.tolist()))

    def criteria(candidate: Candidate) -> tuple[float, float, float]:
        aic = 2 * candidate.k - 2 * candidate.log_likelihood
        bic = candidate.k * math.log(count) - 2 * candidate.log_likelihood
        ks = float(stats.kstest(values, candidate.cdf).statistic)
        return aic, bic, ks

    aic, bic, ks = criteria(chosen)
    comparison = sorted(
        ([candidate.label, *criteria(candidate)] for candidate in candidates.values()),
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
        warnings=chosen.notes + skipped,
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


def power_spectrum(
    values: NDArray[np.float64], time_ms: NDArray[np.int64], seq: NDArray[np.int64]
) -> AnalysisResult:
    return pooled_power_spectrum([("Run", values, time_ms, seq)])


def pooled_power_spectrum(runs: list[SpectrumRun]) -> AnalysisResult:
    usable = [run for run in runs if len(run[1]) >= 32]
    if not usable:
        return AnalysisResult(status="unavailable", reason="PSD needs at least 32 eligible samples per run")
    result = _pooled_spectrum(usable)
    if result.status == "ok" and len(usable) < len(runs):
        short = ", ".join(run_id for run_id, values, _, _ in runs if len(values) < 32)
        result.warnings.insert(0, f"Skipped runs with fewer than 32 eligible samples: {short}")
    return result


def _pooled_spectrum(runs: list[SpectrumRun]) -> AnalysisResult:
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
    if not regular or np.any(np.abs(all_deltas - period) > 0.05 * period):
        return _lomb_scargle_spectrum(runs)
    fs = 1000 / period
    segment = min(256, *(len(values) for _, values, _, _ in runs))
    count = sum(len(values) for _, values, _, _ in runs)
    density = np.zeros(segment // 2 + 1)
    for _, values, _, _ in runs:
        frequencies, run_density = signal.welch(
            values,
            fs=fs,
            window="hann",
            nperseg=segment,
            noverlap=segment // 2,
            detrend="constant",
            scaling="density",
        )
        density += run_density * (len(values) / count)
    return AnalysisResult(
        warnings=[
            "Sample-weighted Welch spectra from all selected run samples; "
            "segments never cross run boundaries. "
            "Each segment is demeaned; intervals are not compressed into a continuous signal."
        ],
        metrics=[
            Metric(key="count", label="Eligible samples", value=count),
            Metric(key="runs", label="Runs", value=len(runs)),
            Metric(key="fs_hz", label="Device-timed sample rate", value=fs, unit="Hz"),
            Metric(key="segment", label="Welch segment", value=segment, unit="samples"),
            Metric(key="resolution_hz", label="Frequency resolution", value=fs / segment, unit="Hz"),
        ],
        plots=[
            Plot(
                x_label="Frequency (Hz)",
                y_label="PSD (m^2/Hz)",
                series=[Series(label="Pooled Welch PSD", x=frequencies.tolist(), y=density.tolist())],
            )
        ],
    )


def _lomb_scargle_spectrum(runs: list[SpectrumRun]) -> AnalysisResult:
    spans = [float(times[-1] - times[0]) / 1000 for _, _, times, _ in runs]
    sample_rates = [1000 / float(np.median(np.diff(times))) for _, _, times, _ in runs]
    minimum_hz = 1 / min(spans)
    maximum_hz = min(sample_rates) / 2
    if minimum_hz >= maximum_hz:
        return AnalysisResult(
            status="unavailable", reason="Selected runs have no common resolvable frequency band"
        )
    points = min(4096, max(256, math.ceil(5 * min(spans) * (maximum_hz - minimum_hz))))
    frequencies = np.linspace(minimum_hz, maximum_hz, points, endpoint=False)
    angular = 2 * np.pi * frequencies
    count = sum(len(values) for _, values, _, _ in runs)
    power = np.zeros(points)
    for _, values, times, _ in runs:
        centered = values - np.mean(values)
        seconds = (times - times[0]).astype(np.float64) / 1000
        # 2/N converts unnormalized Lomb-Scargle power to explained mean-square units.
        for start in range(0, points, 128):
            stop = start + 128
            power[start:stop] += (
                2 * signal.lombscargle(seconds, centered, angular[start:stop], normalize=False) / count
            )
    missing = sum(int(np.sum(np.diff(seq) - 1)) for _, _, _, seq in runs)
    return AnalysisResult(
        warnings=[
            "Gapped or irregular timing: using Lomb-Scargle on every eligible sample's "
            "actual device timestamp. "
            "No interpolation, gap compression or concatenation across runs.",
            "Sample-weighted, mean-subtracted power (2/N scaling, m^2), not a PSD density (m^2/Hz); "
            "do not compare its vertical scale directly with Welch PSD. Frequency grid excludes DC and "
            "ends below the slowest run's median-cadence Nyquist estimate.",
        ],
        metrics=[
            Metric(key="count", label="Eligible samples", value=count),
            Metric(key="runs", label="Runs", value=len(runs)),
            Metric(key="missing_samples", label="Missing sequence numbers", value=missing),
            Metric(
                key="resolution_hz",
                label="Frequency grid step",
                value=float(frequencies[1] - frequencies[0]),
                unit="Hz",
            ),
        ],
        plots=[
            Plot(
                x_label="Frequency (Hz)",
                y_label="Power (m^2)",
                series=[
                    Series(label="Pooled Lomb-Scargle spectrum", x=frequencies.tolist(), y=power.tolist())
                ],
            )
        ],
    )
