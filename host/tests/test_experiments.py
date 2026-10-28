from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from uwb_lab.core import LabState, SessionRecorder


def sample(seq=1, valid=True):
    return {
        "type": "range",
        "v": 1,
        "board": 1,
        "peer": 2,
        "seq": seq,
        "time_ms": seq * 100,
        "raw_m": 2.01,
        "corrected_m": 2.02,
        "valid": valid,
        "expected_m": 2.0,
        "prior_revision": 1,
        "host_time": "2026-10-17T10:00:00+00:00",
    }


def setup_runner(tmp_path):
    from uwb_lab.experiments import BatchRunner

    clock = [0.0]
    state = LabState()
    prior = state.set_prior({"mode": "distance", "distance_m": 2.0})
    runner = BatchRunner(tmp_path, SessionRecorder(tmp_path), clock=lambda: clock[0])
    return runner, clock, prior


def test_batch_deadlines_interval_retention_and_reload(tmp_path):
    from uwb_lab.experiments import BatchInput, BatchRunner

    runner, clock, prior = setup_runner(tmp_path)
    runner.start(
        BatchInput(name="LOS", duration_s=10, run_count=2, interval_s=3),
        source="demo",
        prior=prior,
        radio_snapshot=None,
    )
    batch_id = runner.status()["batch_id"]
    for seq in range(1, 2006):
        runner.record_sample(sample(seq))
    clock[0] = 10
    runner.record_sample(sample(2006))
    assert runner.status()["phase"] == "interval"
    clock[0] = 13
    runner.record_sample(sample(2007))
    assert runner.status()["run_index"] == 2
    clock[0] = 23
    runner.advance()
    assert runner.status()["outcome"] == "completed"
    saved = runner.load_batch(batch_id)
    assert [run["state"] for run in saved["runs"]] == ["completed", "completed"]
    assert saved["runs"][0]["summary"]["count"] == 2005
    path = tmp_path / "experiments" / batch_id
    assert len((path / "intervals.ndjson").read_text().splitlines()) == 1
    assert len((path / saved["runs"][0]["session"] / "telemetry.ndjson").read_text().splitlines()) == 2005
    fresh = BatchRunner(tmp_path, SessionRecorder(tmp_path))
    assert fresh.load_batch(batch_id)["outcome"] == "completed"


def test_interruption_and_no_samples_preserve_outcomes(tmp_path):
    from uwb_lab.experiments import BatchInput

    runner, clock, prior = setup_runner(tmp_path)
    config = BatchInput(duration_s=5, run_count=3, interval_s=0)
    runner.start(config, source="demo", prior=prior, radio_snapshot=None)
    runner.record_sample(sample())
    clock[0] = 5
    runner.advance()
    runner.record_sample(sample(2))
    runner.request_stop("interrupted", "Disconnected")
    runner.advance()
    saved = runner.load_batch(runner.status()["batch_id"])
    assert [run["state"] for run in saved["runs"]] == ["completed", "incomplete", "not_executed"]
    run_path = tmp_path / "experiments" / saved["batch_id"] / saved["runs"][1]["session"]
    assert not json.loads((run_path / "manifest.json").read_text())["completed"]
    runner.start(config, source="demo", prior=prior, radio_snapshot=None)
    clock[0] = 10
    runner.advance()
    assert runner.status()["outcome"] == "failed"
    assert "No valid" in runner.status()["error"]


def test_input_validation_and_safe_ids(tmp_path):
    from uwb_lab.experiments import BatchInput

    for fields in (
        {"duration_s": 0},
        {"duration_s": float("nan")},
        {"run_count": True},
        {"run_count": 1.5},
        {"interval_s": -1},
    ):
        with pytest.raises(ValidationError):
            BatchInput(**fields)
    runner, _, _ = setup_runner(tmp_path)
    with pytest.raises(ValueError):
        runner.load_batch("../escape")


def test_prior_write_failure_releases_shared_recorder(tmp_path, monkeypatch):
    from uwb_lab.experiments import BatchInput

    runner, _, prior = setup_runner(tmp_path)

    def fail_prior(value):
        raise OSError("prior write failed")

    monkeypatch.setattr(runner.recorder, "record_prior", fail_prior)
    with pytest.raises(OSError, match="prior write"):
        runner.start(BatchInput(), source="demo", prior=prior, radio_snapshot=None)
    assert not runner.active
    assert not runner.recorder.active
    assert runner.meta["runs"][0]["state"] == "failed"


def test_manifest_failure_does_not_crash_supervisor(tmp_path, monkeypatch):
    import asyncio

    from uwb_lab.experiments import BatchInput

    runner, clock, prior = setup_runner(tmp_path)
    runner.start(BatchInput(duration_s=1), source="demo", prior=prior, radio_snapshot=None)
    runner.record_sample(sample())

    def fail_manifest():
        raise OSError("manifest disk failure")

    monkeypatch.setattr(runner, "_persist", fail_manifest)
    clock[0] = 1
    asyncio.run(runner.supervise())
    assert not runner.active
    assert runner.status()["outcome"] == "failed"
    assert "disk failure" in runner.status()["error"]


def test_failed_session_finalization_never_marks_run_completed(tmp_path, monkeypatch):
    import asyncio

    from uwb_lab import core
    from uwb_lab.experiments import BatchInput

    runner, clock, prior = setup_runner(tmp_path)
    runner.start(BatchInput(duration_s=1, run_count=2), source="demo", prior=prior, radio_snapshot=None)
    session = runner.recorder.session_dir
    original = core.write_json_atomic

    def fail_session_manifest(path, value):
        if path == session / "manifest.json":
            raise OSError("session finalization disk error")
        original(path, value)

    monkeypatch.setattr(core, "write_json_atomic", fail_session_manifest)
    runner.record_sample(sample())
    clock[0] = 1
    asyncio.run(runner.supervise())
    batch = runner.load_batch(runner.status()["batch_id"])
    assert batch["outcome"] == "failed"
    assert batch["runs"][0]["state"] == "failed"
    assert "session finalization" in batch["runs"][0]["reason"]
    assert batch["runs"][0]["session"] is not None
    assert batch["runs"][1]["state"] == "not_executed"
    assert not runner.recorder.active


def test_start_delay_and_manual_runs(tmp_path):
    from uwb_lab.experiments import BatchInput

    runner, clock, prior = setup_runner(tmp_path)
    runner.start(
        BatchInput(duration_s=10, run_count=2, interval_s=0, start_delay_s=5, manual_start=True),
        source="demo",
        prior=prior,
        radio_snapshot=None,
    )
    assert runner.status()["phase"] == "waiting"
    clock[0] = 100
    runner.record_sample(sample(1))
    assert runner.status()["phase"] == "waiting"
    runner.begin_waiting_run()
    assert runner.status()["phase"] == "delay"
    clock[0] = 104
    runner.advance()
    assert runner.status()["phase"] == "delay"
    clock[0] = 105
    runner.record_sample(sample(2))
    assert runner.status()["phase"] == "recording"
    clock[0] = 115
    runner.record_sample(sample(3))
    status = runner.status()
    assert (status["phase"], status["run_index"]) == ("waiting", 2)
    runner.stop("stopped", "test")


def test_start_delay_automatic(tmp_path):
    from uwb_lab.experiments import BatchInput

    runner, clock, prior = setup_runner(tmp_path)
    runner.start(
        BatchInput(duration_s=10, run_count=1, start_delay_s=5),
        source="demo",
        prior=prior,
        radio_snapshot=None,
    )
    assert runner.status()["phase"] == "delay"
    clock[0] = 5
    runner.advance()
    assert runner.status()["phase"] == "recording"
    runner.stop("stopped", "test")
