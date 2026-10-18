from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy import signal, stats
from sklearn.exceptions import ConvergenceWarning
from sklearn.mixture import GaussianMixture

METHODS = {
    "statistics": {"label": "Residual statistics", "parameters": []},
    "histogram": {"label": "Histogram + Gaussian", "parameters": ["histogram_bins"]},
    "psd": {"label": "Spectrum / PSD", "parameters": []},
    "gmm": {"label": "Gaussian mixture", "parameters": ["components", "seed"]},
}

SpectrumRun = tuple[str, NDArray[np.float64], NDArray[np.int64], NDArray[np.int64]]


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    batch_id: str
    scope: Literal["current", "run", "batch"] = "batch"
    run_id: str | None = None
    mode: Literal["cumulative", "live", "full"] = "cumulative"
    field: Literal["raw_m", "corrected_m"] = "corrected_m"
    methods: list[Literal["statistics", "histogram", "psd", "gmm"]] = Field(
        default_factory=lambda: list(METHODS)
    )
    window_size: int = Field(default=2000, ge=1, le=2000, strict=True)
    histogram_bins: int = Field(default=30, ge=1, le=200, strict=True)
    components: int = Field(default=2, ge=1, le=6, strict=True)
    seed: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)

    @model_validator(mode="after")
    def check_scope(self) -> AnalysisRequest:
        if (self.scope == "run") != (self.run_id is not None):
            raise ValueError("run_id is required exactly when scope is run")
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


def histogram(values: NDArray[np.float64], bins: int) -> AnalysisResult:
    if not len(values):
        return AnalysisResult(status="unavailable", reason="No eligible residuals")
    density, edges = np.histogram(values, bins=bins, density=True)
    series = [
        Series(
            label="Empirical density",
            x=((edges[:-1] + edges[1:]) / 2).tolist(),
            y=density.tolist(),
            style="bars",
        )
    ]
    mean, std = stats.norm.fit(values)
    notices = []
    if std == 0:
        notices.append("Constant residuals: Gaussian density is degenerate and is not drawn")
    else:
        grid = np.linspace(min(edges[0], mean - 4 * std), max(edges[-1], mean + 4 * std), 250)
        series.append(
            Series(label="Gaussian baseline", x=grid.tolist(), y=stats.norm.pdf(grid, mean, std).tolist())
        )
    return AnalysisResult(
        warnings=notices,
        metrics=[
            Metric(key="mean_m", label="Gaussian mean", value=float(mean), unit="m"),
            Metric(key="sigma_m", label="Gaussian MLE spread", value=float(std), unit="m"),
        ],
        plots=[Plot(x_label="Residual (m)", y_label="Density (1/m)", series=series)],
    )


def power_spectrum(
    values: NDArray[np.float64], time_ms: NDArray[np.int64], seq: NDArray[np.int64]
) -> AnalysisResult:
    return pooled_power_spectrum([("Run", values, time_ms, seq)])


def pooled_power_spectrum(
    runs: list[SpectrumRun],
) -> AnalysisResult:
    if not runs:
        return AnalysisResult(status="unavailable", reason="PSD needs at least 32 eligible samples per run")
    deltas = []
    regular = True
    for run_id, values, time_ms, seq in runs:
        if len(values) < 32:
            return AnalysisResult(
                status="unavailable", reason=f"{run_id}: PSD needs at least 32 eligible samples per run"
            )
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


def fit_gmm(values: NDArray[np.float64], components: int, seed: int) -> AnalysisResult:
    if len(values) < max(10, 5 * components) or len(np.unique(values)) < max(2, components):
        return AnalysisResult(status="unavailable", reason="Too few samples or distinct values for this GMM")
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
        return AnalysisResult(status="failed", reason="GMM did not converge")
    means = model.means_.ravel()
    spreads = np.sqrt(model.covariances_.ravel())
    order = np.argsort(means)
    grid = np.linspace(
        float(min(values.min(), np.min(means - 4 * spreads))),
        float(max(values.max(), np.max(means + 4 * spreads))),
        300,
    )
    curves = []
    total = np.zeros_like(grid)
    rows = []
    for index in order:
        weight, mean, std = float(model.weights_[index]), float(means[index]), float(spreads[index])
        density = weight * stats.norm.pdf(grid, mean, std)
        total += density
        rows.append([weight, mean, std])
        curves.append(Series(label=f"Component {len(rows)}", x=grid.tolist(), y=density.tolist()))
    curves.insert(0, Series(label="Mixture density", x=grid.tolist(), y=total.tolist()))
    return AnalysisResult(
        warnings=[str(item.message) for item in caught]
        + ["Exploratory components are not LOS/NLOS labels; covariance regularization = 1e-10 m^2"],
        metrics=[
            Metric(key="aic", label="AIC", value=float(model.aic(matrix))),
            Metric(key="bic", label="BIC", value=float(model.bic(matrix))),
            Metric(key="log_likelihood", label="Mean log likelihood", value=float(model.score(matrix))),
            Metric(key="converged", label="Converged", value=1),
        ],
        tables=[Table(columns=["Weight", "Mean (m)", "Spread (m)"], rows=rows)],
        plots=[Plot(x_label="Residual (m)", y_label="Density (1/m)", series=curves)],
    )
