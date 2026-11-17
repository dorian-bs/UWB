from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


def test_analysis_contracts():
    from uwb_lab.analysis import AnalysisRequest, AnalysisResult

    request = AnalysisRequest(batch_id="batch-a", scope="run", run_id="run-1")
    assert request.field == "corrected_m"
    assert request.mode == "cumulative"
    assert AnalysisRequest(batch_id="batch-a", fit="auto").fit == "auto"
    assert AnalysisRequest(batch_id="batch-a", compare_show_fits=False).compare_show_fits is False
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
    auto = distribution_fit(values, 25, "auto", 2, 0)
    assert auto.status == "ok"
    assert auto.plots[0].series[1].label in {row[0] for row in auto.tables[1].rows}


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
    for name, count, amplitude in [("first", 512, 1), ("second", 1024, 3)]:
        times = np.arange(count) * 100
        values = amplitude * np.sin(2 * np.pi * 1.25 * times / 1000)
        runs.append((name, values, times, np.arange(count)))
        frequencies, density = signal.welch(
            values,
            fs=10,
            window="hann",
            nperseg=256,
            noverlap=128,
            detrend="constant",
            scaling="density",
        )
        expected.append(density * count)
    result = pooled_power_spectrum(runs)
    assert result.status == "ok"
    curve = result.plots[0].series[0]
    np.testing.assert_allclose(curve.x, frequencies)
    np.testing.assert_allclose(curve.y, sum(expected) / 1536, rtol=1e-7, atol=1e-12 * max(curve.y))
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25)
    metrics = {metric.key: metric.value for metric in result.metrics}
    assert metrics["count"] == 1536
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
    assert plot.y_label == "PSD (m^2/Hz)"
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25, abs=0.04)
    # The density integrates to the variance of the tone (1/2), as a Welch PSD would.
    assert np.sum(curve.y) * (curve.x[1] - curve.x[0]) == pytest.approx(0.5, rel=0.15)
    metrics = {metric.key: metric.value for metric in result.metrics}
    assert metrics["count"] == int(keep.sum())
    assert metrics["missing_samples"] > 0
    assert metrics["peak_hz"] == pytest.approx(1.25, abs=0.04)
    assert any("actual device timestamps" in notice for notice in result.warnings)
    json.dumps(result.model_dump(), allow_nan=False)


def test_gapped_pooling_is_sample_weighted_and_does_not_join_pauses():
    import numpy as np

    from uwb_lab.analysis import pooled_power_spectrum

    runs = []
    for run_id, count, amplitude in [("first", 600, 1), ("second", 1200, 2)]:
        sequences = np.arange(count)
        keep = sequences % 11 != 0
        times = sequences * 100 + (0 if run_id == "first" else 10000000)
        values = 12 + amplitude * np.sin(2 * np.pi * 1.25 * sequences / 10)
        runs.append((run_id, values[keep], times[keep], sequences[keep]))
    result = pooled_power_spectrum(runs)
    assert result.status == "ok"
    curve = result.plots[0].series[0]
    assert "Lomb-Scargle" in curve.label
    first = pooled_power_spectrum(runs[:1]).plots[0].series[0].y
    second = pooled_power_spectrum(runs[1:]).plots[0].series[0].y
    weights = [len(run[1]) for run in runs]
    np.testing.assert_allclose(
        curve.y, (weights[0] * np.array(first) + weights[1] * np.array(second)) / sum(weights)
    )
    assert curve.x[int(np.argmax(curve.y))] == pytest.approx(1.25, abs=0.04)
    assert max(curve.x) <= 5
    metrics = {metric.key: metric.value for metric in result.metrics}
    assert metrics["count"] == sum(weights)
    assert metrics["runs"] == 2


def test_spectrum_grid_does_not_move_while_a_new_run_is_still_filling():
    import numpy as np

    from uwb_lab.analysis import pooled_power_spectrum

    rng = np.random.default_rng(5)

    def run(name, count):
        sequences = np.arange(count)
        keep = rng.random(count) > 0.03
        return name, rng.normal(0, 0.01, count)[keep], (sequences * 33)[keep], sequences[keep]

    finished = [run("a-run-0001", 1800), run("a-run-0002", 1800)]
    grids = []
    for filling in (40, 100, 255):
        result = pooled_power_spectrum([*finished, run("a-run-0003", filling)])
        curve = result.plots[0].series[0]
        grids.append((curve.x, {m.key: m.value for m in result.metrics}))
        assert any("Left out runs" in notice and "a-run-0003" in notice for notice in result.warnings)
    for x, metrics in grids[1:]:
        assert x == grids[0][0]
        assert metrics["segment"] == grids[0][1]["segment"] == 256
        assert metrics["runs"] == 2
    joined = pooled_power_spectrum([*finished, run("a-run-0003", 400)])
    assert {m.key: m.value for m in joined.metrics}["runs"] == 3
    assert joined.plots[0].series[0].x == grids[0][0]


def test_spectrum_flags_a_wandering_tone_and_separates_it_from_white_noise():
    import numpy as np

    from uwb_lab.analysis import pooled_power_spectrum

    rng = np.random.default_rng(11)
    fs = 30.0

    def runs(tone):
        out = []
        for index in range(4):
            sequences = np.arange(1800)
            times = np.round(sequences * 1000 / fs).astype(np.int64)
            values = rng.normal(0, 0.01, len(sequences)) + tone * np.sin(2 * np.pi * 7.5 * sequences / fs)
            out.append((f"x-run-{index + 1:04d}", values, times, sequences))
        return out

    white = pooled_power_spectrum(runs(0))
    tonal = pooled_power_spectrum(runs(0.02))
    white_metrics = {m.key: m.value for m in white.metrics}
    tonal_metrics = {m.key: m.value for m in tonal.metrics}
    assert white_metrics["narrowband_pct"] == 0
    assert white_metrics["peak_excess_db"] < 4
    assert white_metrics["flatness"] > 0.95
    assert abs(white_metrics["acf_lag2"]) < 0.05
    assert tonal_metrics["peak_hz"] == pytest.approx(7.5, abs=0.15)
    assert tonal_metrics["peak_excess_db"] > 15
    assert tonal_metrics["narrowband_pct"] > 90
    assert tonal_metrics["acf_lag2"] < -0.5
    assert tonal_metrics["flatness"] < 0.5
    spectrum, spectrogram, correlation = tonal.plots
    assert spectrum.x_scale == spectrum.y_scale == "log"
    assert spectrum.annotations and "7.5" in spectrum.annotations[0].text
    assert {series.style for series in spectrum.series} >= {"line", "band"}
    heatmap = spectrogram.heatmap
    assert len(heatmap.z) == len(heatmap.y) and len(heatmap.z[0]) == len(heatmap.x)
    assert len(heatmap.markers) == 3
    ridge = int(np.argmin(np.abs(np.array(heatmap.y) - 7.5)))
    assert np.mean(heatmap.z[ridge]) > np.mean(heatmap.z) + 10
    assert correlation.series[0].style == "stems"
    assert [table.title for table in tonal.tables] == ["Dominant tone per run", "Whiteness check"]
    assert len(tonal.tables[0].rows) == 4 and tonal.tables[0].rows[0][0] == "#1"
    assert any(row[3] == "correlated" for row in tonal.tables[1].rows)
    json.dumps(tonal.model_dump(), allow_nan=False)


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
