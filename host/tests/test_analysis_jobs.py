from __future__ import annotations

import asyncio
import json
import threading

import numpy as np
import pytest

from uwb_lab.core import LabState, SessionRecorder
from uwb_lab.experiments import BatchInput, BatchRunner


def archive(tmp_path):
    clock = [0.0]
    state = LabState()
    prior = state.set_prior({"mode": "distance", "distance_m": 2.0})
    runner = BatchRunner(tmp_path, SessionRecorder(tmp_path), clock=lambda: clock[0])
    runner.start(
        BatchInput(duration_s=40, run_count=2, interval_s=1), source="demo", prior=prior, radio_snapshot=None
    )
    for run_index in range(2):
        for seq in range(40):
            clock[0] = run_index * 41 + seq
            runner.record_sample(
                {
                    "type": "range",
                    "v": 1,
                    "board": 1,
                    "peer": 2,
                    "seq": run_index * 41 + seq,
                    "time_ms": int(clock[0] * 1000),
                    "raw_m": 2.1 + (seq % 5) * 0.001,
                    "corrected_m": 2.02 + (seq % 5) * 0.001,
                    "valid": seq != 39,
                    "expected_m": 2.0,
                    "prior_revision": 1,
                    "host_time": "2026-10-17T10:00:00+00:00",
                }
            )
        clock[0] = run_index * 41 + 40
        runner.advance()
    return runner


def test_saved_analysis_provenance_exclusions_and_run_isolation(tmp_path):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        runner = archive(tmp_path)
        batch_id = runner.status()["batch_id"]
        service = AnalysisService(runner)
        job = service.submit(AnalysisRequest(batch_id=batch_id, scope="batch", mode="full"))
        await service.close()
        result = service.get(job["job_id"])
        assert result["state"] == "succeeded", result
        output = result["output"]
        assert output["included_count"] == 78
        assert output["excluded"]["invalid"] == 2
        spectra = [item for item in output["results"] if item["method"] == "psd"]
        assert len(spectra) == 1
        assert spectra[0]["run_id"] is None
        assert spectra[0]["result"]["status"] == "ok"
        assert output["provenance"]["prior_revisions"] == [1]
        assert output["provenance"]["source_sha256"]
        statistics = next(item["result"] for item in output["results"] if item["method"] == "statistics")
        assert len(statistics["metrics"]) == 5
        assert statistics["tables"] == []
        path = runner.batch_path(batch_id) / "analyses" / f"{job['job_id']}.json"
        assert json.loads(path.read_text())["recipe"]["field"] == "corrected_m"
        json.dumps(output, allow_nan=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["cumulative", "full"])
def test_analysis_updates_spectrum_after_a_missing_recorded_sample(tmp_path, mode):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        runner = archive(tmp_path)
        batch_id = runner.status()["batch_id"]
        run = runner.load_batch(batch_id)["runs"][1]
        path = runner.batch_path(batch_id) / run["session"] / "telemetry.ndjson"
        records = path.read_text().splitlines()
        path.write_text("\n".join(records[:20] + records[21:]) + "\n")
        service = AnalysisService(runner)
        job = service.submit(AnalysisRequest(batch_id=batch_id, mode=mode, methods=["psd"]))
        await service.close()
        result = service.get(job["job_id"])
        assert result["state"] == "succeeded", result
        output = result["output"]
        assert output["included_count"] == 77
        spectrum = output["results"][0]["result"]
        assert spectrum["status"] == "ok", spectrum
        assert "Lomb-Scargle" in spectrum["plots"][0]["series"][0]["label"]
        assert spectrum["plots"][0]["y_label"] == "Power (m^2)"
        metrics = {metric["key"]: metric["value"] for metric in spectrum["metrics"]}
        assert metrics["count"] == 77
        assert metrics["missing_samples"] == 1
        assert metrics["runs"] == 2
        json.dumps(output, allow_nan=False)

    asyncio.run(scenario())


def test_analysis_worker_coalesces_pending_requests(tmp_path):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        runner = archive(tmp_path)
        service = AnalysisService(runner)
        started, release = threading.Event(), threading.Event()
        original = service.compute

        def blocking(snapshot):
            started.set()
            assert release.wait(5)
            return original(snapshot)

        service.compute = blocking
        first = service.submit(AnalysisRequest(batch_id=runner.status()["batch_id"], methods=["statistics"]))
        await asyncio.to_thread(started.wait, 5)
        second = service.submit(
            AnalysisRequest(
                batch_id=runner.status()["batch_id"],
                methods=["statistics"],
                window_size=10,
            )
        )
        third = service.submit(
            AnalysisRequest(
                batch_id=runner.status()["batch_id"],
                methods=["statistics"],
                window_size=20,
            )
        )
        assert service.get(second["job_id"])["state"] == "superseded"
        release.set()
        await service.close()
        assert service.get(first["job_id"])["state"] == "succeeded"
        assert service.get(third["job_id"])["state"] == "succeeded"

    asyncio.run(scenario())


def test_window_count_does_not_claim_full_selection_size(tmp_path):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        runner = archive(tmp_path)
        service = AnalysisService(runner)
        job = service.submit(
            AnalysisRequest(
                batch_id=runner.status()["batch_id"],
                scope="batch",
                mode="live",
                window_size=10,
                methods=["statistics"],
            )
        )
        await service.close()
        output = service.get(job["job_id"])["output"]
        metrics = {item["key"]: item["value"] for item in output["results"][0]["result"]["metrics"]}
        assert output["included_count"] == 10
        assert metrics["count"] == 78
        assert metrics["window_count"] == 10

    asyncio.run(scenario())


def test_cumulative_analysis_fits_every_sample_not_run_means_or_a_window(tmp_path):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        runner = archive(tmp_path)
        batch_id = runner.status()["batch_id"]
        batch = runner.load_batch(batch_id)
        residuals = []
        for index, run in enumerate(batch["runs"]):
            values = np.linspace(-0.2, 0.1, 1000) if index == 0 else np.linspace(0.1, 0.4, 2200)
            residuals.extend(values)
            path = runner.batch_path(batch_id) / run["session"] / "telemetry.ndjson"
            template = json.loads(path.read_text().splitlines()[0])
            with path.open("w") as stream:
                for seq, value in enumerate(values):
                    sample = {**template, "seq": seq, "time_ms": seq * 100, "corrected_m": float(2 + value)}
                    stream.write(json.dumps(sample) + "\n")
        service = AnalysisService(runner)
        job = service.submit(
            AnalysisRequest(batch_id=batch_id, methods=["statistics", "histogram", "psd", "gmm"])
        )
        await service.close()
        result = service.get(job["job_id"])
        assert result["state"] == "succeeded", result
        output = result["output"]
        assert output["included_count"] == 3200
        assert output["window_size"] is None
        results = {item["method"]: item["result"] for item in output["results"]}
        metrics = {item["key"]: item["value"] for item in results["statistics"]["metrics"]}
        assert metrics["count"] == 3200
        assert metrics["bias_m"] == pytest.approx(np.mean(residuals))
        assert metrics["std_m"] == pytest.approx(np.std(residuals, ddof=1))
        gaussian = {item["key"]: item["value"] for item in results["histogram"]["metrics"]}
        assert gaussian["mean_m"] == pytest.approx(np.mean(residuals))
        assert gaussian["sigma_m"] == pytest.approx(np.std(residuals))
        assert results["psd"]["status"] == "ok"
        spectrum_metrics = {item["key"]: item["value"] for item in results["psd"]["metrics"]}
        assert spectrum_metrics["count"] == 3200
        assert results["gmm"]["status"] == "ok"
        assert sum(row[0] * row[1] for row in results["gmm"]["tables"][0]["rows"]) == pytest.approx(
            np.mean(residuals), abs=1e-6
        )

    asyncio.run(scenario())


def test_cumulative_live_snapshot_includes_older_samples_but_not_later_appends(tmp_path):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        state = LabState()
        prior = state.set_prior({"mode": "distance", "distance_m": 2.0})
        runner = BatchRunner(tmp_path, SessionRecorder(tmp_path), clock=lambda: 0)
        runner.start(
            BatchInput(duration_s=10000, run_count=1), source="demo", prior=prior, radio_snapshot=None
        )
        service = AnalysisService(runner)
        batch_id = runner.status()["batch_id"]
        for seq in range(2101):
            sample = {
                "seq": seq,
                "time_ms": seq * 1000,
                "board": 1,
                "peer": 2,
                "raw_m": 2.1,
                "corrected_m": 2.02,
                "expected_m": 2.0,
                "valid": True,
            }
            run_id = runner.record_sample(sample)
            service.observe(batch_id, run_id, sample)
        started, release = threading.Event(), threading.Event()
        compute = service.compute

        def blocked(snapshot):
            started.set()
            assert release.wait(5)
            return compute(snapshot)

        service.compute = blocked
        job = service.submit(AnalysisRequest(batch_id=batch_id, methods=["statistics"]))
        assert await asyncio.to_thread(started.wait, 5)
        runner.record_sample({**sample, "seq": 2101, "time_ms": 2101000, "corrected_m": 99})
        release.set()
        await service.close()
        result = service.get(job["job_id"])
        assert result["state"] == "succeeded", result
        assert result["output"]["included_count"] == 2101
        metrics = {
            metric["key"]: metric["value"] for metric in result["output"]["results"][0]["result"]["metrics"]
        }
        assert metrics["bias_m"] == pytest.approx(0.02)
        runner.stop("stopped", None)

    asyncio.run(scenario())


def test_terminal_batch_state_bypasses_stale_live_throttle(tmp_path):
    from uwb_lab.analysis import AnalysisRequest
    from uwb_lab.analysis_jobs import AnalysisService

    async def scenario():
        runner = archive(tmp_path)
        runner.meta["outcome"] = "running"
        service = AnalysisService(runner)
        request = AnalysisRequest(batch_id=runner.status()["batch_id"], methods=["statistics"])
        first = service.submit(request)
        runner.meta["outcome"] = "completed"
        second = service.submit(request)
        assert first["job_id"] != second["job_id"]
        await service.close()

    asyncio.run(scenario())
