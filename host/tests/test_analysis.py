from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


def test_analysis_contracts():
    from uwb_lab.analysis import AnalysisRequest, AnalysisResult

    request = AnalysisRequest(batch_id="batch-a", scope="run", run_id="run-1")
    assert request.field == "corrected_m"
    assert request.mode == "cumulative"
    assert AnalysisRequest(batch_id="batch-a").scope == "batch"
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", scope="run")
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", components=7)
    result = AnalysisResult(status="unavailable", reason="No valid samples")
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("retired", [{"mode": "live"}, {"window_size": 10}])
def test_analysis_rejects_retired_window_parameters(retired):
    from uwb_lab.analysis import AnalysisRequest

    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate({"batch_id": "batch-a", **retired})


def test_summary_and_running_moments():
    import numpy as np

    from uwb_lab.analysis import Moments, describe

    values = np.array([-0.01, 0, 0.02, 0.03])
    result = describe(values)
    metrics = {item.key: item.value for item in result.metrics}
    assert metrics["bias_m"] == pytest.approx(0.01)
    assert metrics["std_m"] == pytest.approx(np.std(values, ddof=1))
    assert metrics["rmse_m"] == pytest.approx(np.sqrt(np.mean(values**2)))
    assert metrics["p95_abs_m"] == pytest.approx(np.quantile(np.abs(values), 0.95))
    assert set(metrics) == {"count", "bias_m", "std_m", "rmse_m", "p95_abs_m"}
    moments = Moments()
    for value in values:
        moments.add(float(value))
    assert moments.summary()["bias_m"] == pytest.approx(metrics["bias_m"])
    assert moments.summary()["std_m"] == pytest.approx(metrics["std_m"])
    assert describe(np.array([])).status == "unavailable"
    assert {m.key: m.value for m in describe(np.array([1.0])).metrics}["std_m"] is None


def test_distribution_degenerate_inputs_and_retired_acf():
    import numpy as np

    from uwb_lab.analysis import METHODS, AnalysisRequest, distribution_fit

    assert "acf" not in METHODS
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", methods=["acf"])
    constant = distribution_fit(np.ones(40), 10, "gaussian", 2, 0)
    assert constant.warnings
    json.dumps(constant.model_dump(), allow_nan=False)
    normal = distribution_fit(np.random.default_rng(4).normal(size=1000), 20, "gaussian", 2, 0)
    assert len(normal.plots[0].series) == 2
    assert normal.status == "ok"


def test_every_distribution_fit_draws_histogram_plus_curve_and_compares_models():
    import numpy as np

    from uwb_lab.analysis import FITS, distribution_fit

    values = np.random.default_rng(8).gamma(4.0, 0.01, 1500) - 0.04
    for name in FITS:
        result = distribution_fit(values, 25, name, 2, 0)
        assert result.status == "ok", (name, result.reason)
        assert result.plots[0].series[0].style == "bars"
        assert result.plots[0].series[1].label.startswith(FITS[name].split(" (")[0])
        assert {metric.key for metric in result.metrics} == {"log_likelihood", "aic", "bic", "ks"}
        json.dumps(result.model_dump(), allow_nan=False)
    comparison = result.tables[1]
    assert len(comparison.rows) == len(FITS)
    aics = [row[1] for row in comparison.rows]
    assert aics == sorted(aics)
    assert comparison.rows[0][0].startswith("Log-normal") or comparison.rows[0][0].startswith("Skew")


def test_psd_recovers_device_timed_tone_and_checks_thresholds():
    import numpy as np

    from uwb_lab.analysis import power_spectrum

    times = np.arange(1000) * 100
    seq = np.arange(1000)
    values = np.sin(2 * np.pi * 1.25 * times / 1000)
    result = power_spectrum(values, times, seq)
    curve = result.plots[0].series[0]
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25, abs=10 / 256)
    seq[50:] += 1
    gapped = power_spectrum(values, times, seq)
    assert gapped.status == "ok"
    assert "Lomb-Scargle" in gapped.plots[0].series[0].label
    regular = np.arange(40) * 100
    regular[20:] += 5
    assert power_spectrum(values[:40], regular, np.arange(40)).status == "ok"
    regular[20:] += 1
    irregular = power_spectrum(values[:40], regular, np.arange(40))
    assert irregular.status == "ok"
    assert "Lomb-Scargle" in irregular.plots[0].series[0].label
    regular[20] = regular[19]
    assert power_spectrum(values[:40], regular, np.arange(40)).status == "unavailable"


def test_pooled_psd_weights_individual_samples_without_joining_run_boundaries():
    import numpy as np
    from scipy import signal

    from uwb_lab.analysis import pooled_power_spectrum

    runs = []
    expected = []
    for name, count, amplitude in [("first", 128, 1), ("second", 512, 3)]:
        times = np.arange(count) * 100
        values = amplitude * np.sin(2 * np.pi * 1.25 * times / 1000)
        runs.append((name, values, times, np.arange(count)))
        frequencies, density = signal.welch(
            values,
            fs=10,
            window="hann",
            nperseg=128,
            noverlap=64,
            detrend="constant",
            scaling="density",
        )
        expected.append(density * count)
    result = pooled_power_spectrum(runs)
    assert result.status == "ok"
    curve = result.plots[0].series[0]
    np.testing.assert_allclose(curve.x, frequencies)
    np.testing.assert_allclose(curve.y, sum(expected) / 640)
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25)
    metrics = {metric.key: metric.value for metric in result.metrics}
    assert metrics["count"] == 640
    assert metrics["runs"] == 2
    assert result.warnings

    name, values, times, sequences = runs[1]
    sequences[50:] += 1
    gapped = pooled_power_spectrum(runs)
    assert gapped.status == "ok"
    assert "Lomb-Scargle" in gapped.plots[0].series[0].label
    sequences[50:] -= 1
    runs[1] = (name, values, times * 2, sequences)
    different_cadences = pooled_power_spectrum(runs)
    assert different_cadences.status == "ok"
    assert "Lomb-Scargle" in different_cadences.plots[0].series[0].label


def test_gapped_spectrum_recovers_tone_from_actual_timestamps_without_interpolation():
    import numpy as np

    from uwb_lab.analysis import power_spectrum

    rng = np.random.default_rng(24)
    sequences = np.arange(1000)
    keep = rng.random(len(sequences)) > 0.25
    times = sequences * 100 + rng.integers(-10, 11, len(sequences)) + 1000000000
    values = 10 + np.sin(2 * np.pi * 1.25 * (times - times[0]) / 1000)
    result = power_spectrum(values[keep], times[keep], sequences[keep])
    assert result.status == "ok"
    plot = result.plots[0]
    curve = plot.series[0]
    assert "Lomb-Scargle" in curve.label
    assert plot.y_label == "Power (m^2)"
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25, abs=0.015)
    assert max(curve.y) == pytest.approx(0.5, abs=0.03)
    metrics = {metric.key: metric.value for metric in result.metrics}
    assert metrics["count"] == int(keep.sum())
    assert metrics["missing_samples"] > 0
    assert any("not a PSD density" in notice for notice in result.warnings)
    json.dumps(result.model_dump(), allow_nan=False)


def test_lomb_scargle_pooling_is_sample_weighted_and_does_not_join_pauses():
    import numpy as np
    from scipy import signal

    from uwb_lab.analysis import pooled_power_spectrum

    runs = []
    for run_id, count, amplitude in [("first", 480, 1), ("second", 960, 2)]:
        sequences = np.arange(count)
        keep = sequences % 11 != 0
        times = sequences * 100 + (0 if run_id == "first" else 10000000)
        values = 12 + amplitude * np.sin(2 * np.pi * 1.25 * sequences / 10)
        runs.append((run_id, values[keep], times[keep], sequences[keep]))
    result = pooled_power_spectrum(runs)
    assert result.status == "ok"
    curve = result.plots[0].series[0]
    expected = np.zeros(len(curve.x))
    count = sum(len(values) for _, values, _, _ in runs)
    for _, values, times, _ in runs:
        expected += (
            2
            * signal.lombscargle(
                (times - times[0]) / 1000,
                values - np.mean(values),
                2 * np.pi * np.array(curve.x),
                normalize=False,
            )
            / count
        )
    np.testing.assert_allclose(curve.y, expected)
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25, abs=0.015)
    assert max(curve.x) < 5
    metrics = {metric.key: metric.value for metric in result.metrics}
    assert metrics["count"] == count
    assert metrics["runs"] == 2


def test_gapped_spectrum_handles_constants_but_still_rejects_resets_and_short_runs():
    import numpy as np

    from uwb_lab.analysis import power_spectrum

    sequences = np.arange(64)
    times = sequences * 100
    sequences[32:] += 1
    times[32:] += 100
    result = power_spectrum(np.ones(64), times, sequences)
    assert result.status == "ok"
    np.testing.assert_allclose(result.plots[0].series[0].y, 0)
    assert power_spectrum(np.ones(31), times[:31], sequences[:31]).status == "unavailable"
    sequences[40:] -= 40
    reset = power_spectrum(np.ones(64), times, sequences)
    assert reset.status == "unavailable"
    assert "reset" in reset.reason.lower()


def test_gmm_recovers_seeded_components():
    import numpy as np

    from uwb_lab.analysis import distribution_fit

    rng = np.random.default_rng(123)
    values = np.concatenate([rng.normal(-0.04, 0.005, 1500), rng.normal(0.06, 0.008, 500)])
    result = distribution_fit(values, 30, "gmm", 2, 0)
    assert result.status == "ok"
    assert result.tables[0].title == "Components"
    np.testing.assert_allclose([row[1] for row in result.tables[0].rows], [-0.04, 0.06], atol=0.003)
    np.testing.assert_allclose([row[0] for row in result.tables[0].rows], [0.75, 0.25], atol=0.03)
    assert distribution_fit(np.arange(5.0), 5, "gmm", 2, 0).status == "unavailable"
    json.dumps(result.model_dump(), allow_nan=False)
