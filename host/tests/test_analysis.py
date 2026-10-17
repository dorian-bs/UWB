from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


def test_analysis_contracts():
    from uwb_lab.analysis import AnalysisRequest, AnalysisResult

    request = AnalysisRequest(batch_id="batch-a", scope="run", run_id="run-1")
    assert request.field == "corrected_m"
    assert request.window_size == 2000
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", scope="run")
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", components=7)
    with pytest.raises(ValidationError):
        AnalysisRequest(batch_id="batch-a", window_size=True)
    result = AnalysisResult(status="unavailable", reason="No valid samples")
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_summary_and_running_moments():
    import numpy as np

    from uwb_lab.analysis import Moments, describe

    values = np.array([-0.01, 0, 0.02, 0.03])
    result = describe(values)
    metrics = {item.key: item.value for item in result.metrics}
    assert metrics["bias_m"] == pytest.approx(0.01)
    assert metrics["std_m"] == pytest.approx(np.std(values, ddof=1))
    assert metrics["mae_m"] == pytest.approx(0.015)
    assert metrics["rmse_m"] == pytest.approx(np.sqrt(np.mean(values**2)))
    moments = Moments()
    for value in values:
        moments.add(float(value))
    assert moments.summary()["bias_m"] == pytest.approx(metrics["bias_m"])
    assert moments.summary()["std_m"] == pytest.approx(metrics["std_m"])
    assert describe(np.array([])).status == "unavailable"
    assert {m.key: m.value for m in describe(np.array([1.0])).metrics}["std_m"] is None


def test_distribution_and_acf_degenerate_inputs():
    import numpy as np

    from uwb_lab.analysis import autocorrelation, histogram

    assert autocorrelation(np.ones(40), 10).status == "unavailable"
    constant = histogram(np.ones(40), 10)
    assert constant.warnings
    json.dumps(constant.model_dump(), allow_nan=False)
    result = autocorrelation(np.array([1.0, 2, 3, 4]), 2)
    assert result.plots[0].series[0].y[0] == pytest.approx(1.0)
    normal = histogram(np.random.default_rng(4).normal(size=1000), 20)
    assert len(normal.plots[0].series) == 2


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
    assert power_spectrum(values, times, seq).status == "unavailable"
    regular = np.arange(40) * 100
    regular[20:] += 5
    assert power_spectrum(values[:40], regular, np.arange(40)).status == "ok"
    regular[20:] += 1
    assert power_spectrum(values[:40], regular, np.arange(40)).status == "unavailable"


def test_gmm_recovers_seeded_components():
    import numpy as np

    from uwb_lab.analysis import fit_gmm

    rng = np.random.default_rng(123)
    values = np.concatenate([rng.normal(-0.04, 0.005, 1500), rng.normal(0.06, 0.008, 500)])
    result = fit_gmm(values, 2, 0)
    assert result.status == "ok"
    np.testing.assert_allclose([row[1] for row in result.tables[0].rows], [-0.04, 0.06], atol=0.003)
    np.testing.assert_allclose([row[0] for row in result.tables[0].rows], [0.75, 0.25], atol=0.03)
    assert fit_gmm(np.ones(20), 2, 0).status == "unavailable"
    json.dumps(result.model_dump(), allow_nan=False)
