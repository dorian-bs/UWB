from __future__ import annotations

import asyncio
import json
import threading

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
        assert len([item for item in output["results"] if item["method"] == "psd"]) == 2
        assert output["provenance"]["prior_revisions"] == [1]
        assert output["provenance"]["source_sha256"]
        statistics = next(item["result"] for item in output["results"] if item["method"] == "statistics")
        timing = statistics["tables"][0]
        assert timing["columns"] == [
            "Run",
            "Analysed eligible n",
            "Device span (s)",
            "Observed eligible Hz",
            "Recorded duration (s)",
        ]
        assert timing["rows"][0][2:4] == [38, 1]
        path = runner.batch_path(batch_id) / "analyses" / f"{job['job_id']}.json"
        assert json.loads(path.read_text())["recipe"]["field"] == "corrected_m"
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
