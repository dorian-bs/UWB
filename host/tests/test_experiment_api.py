from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    module = importlib.import_module("uwb_lab.app")
    runtime = module.Runtime(data_dir=tmp_path)
    runtime.source = "demo"
    monkeypatch.setattr(module, "runtime", runtime)
    with TestClient(module.app) as connection:
        yield connection


def test_batch_locks_prior_and_manual_recording(client):
    assert client.post("/api/prior", json={"distance_m": 2.0}).status_code == 200
    started = client.post(
        "/api/experiments/start",
        json={
            "name": "LOS",
            "duration_s": 30,
            "run_count": 2,
            "interval_s": 5,
        },
    )
    assert started.status_code == 200
    assert client.post("/api/prior", json={"distance_m": 3.0}).status_code == 409
    assert client.post("/api/recording/start", json={"name": "manual"}).status_code == 409
    assert client.post("/api/recording/stop").status_code == 409
    assert client.post("/api/experiments/start", json={}).status_code == 409
    stopped = client.post("/api/experiments/stop")
    assert stopped.status_code == 200
    batch_id = stopped.json()["batch_id"]
    saved = client.get(f"/api/experiments/{batch_id}").json()
    assert saved["outcome"] == "stopped"
    assert saved["runs"][0]["state"] == "incomplete"
    assert client.post("/api/prior", json={"distance_m": 3.0}).status_code == 200


def test_source_disconnect_is_latched_and_archived(client):
    module = importlib.import_module("uwb_lab.app")
    client.post("/api/prior", json={"distance_m": 2.0})
    batch = client.post("/api/experiments/start", json={"duration_s": 30}).json()
    module.runtime.set_connected(False, "test disconnect")
    module.runtime.set_connected(True)
    module.runtime.experiments.advance()
    saved = client.get(f"/api/experiments/{batch['batch_id']}").json()
    assert saved["outcome"] == "interrupted"
    assert "disconnect" in saved["error"]


def test_batch_requires_prior_and_connected_source(client):
    assert client.post("/api/experiments/start", json={}).status_code == 409
    client.post("/api/prior", json={"distance_m": 2.0})
    module = importlib.import_module("uwb_lab.app")
    module.runtime.set_connected(False)
    assert client.post("/api/experiments/start", json={}).status_code == 409
    assert client.get("/api/experiments/missing").status_code == 404


def test_full_analysis_job_for_saved_run(client):
    client.post("/api/prior", json={"distance_m": 2.0})
    batch = client.post("/api/experiments/start", json={"duration_s": 30, "run_count": 1}).json()
    with client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.receive_json()
    client.post("/api/experiments/stop")
    assert "gmm" in client.get("/api/analysis/methods").json()["methods"]
    submitted = client.post(
        "/api/analysis/jobs",
        json={
            "batch_id": batch["batch_id"],
            "scope": "batch",
            "mode": "full",
        },
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/analysis/jobs/{job_id}").json()
        if job["state"] not in ("queued", "running"):
            break
        time.sleep(0.01)
    assert job["state"] == "succeeded", job
    assert job["output"]["included_count"] >= 1
    assert client.get(f"/api/experiments/{batch['batch_id']}/analyses").json()["analyses"]
    saved = client.get(f"/api/experiments/{batch['batch_id']}/analyses/{job_id}")
    assert saved.status_code == 200
    assert saved.json()["output"]["provenance"] == job["output"]["provenance"]


def test_shutdown_storage_error_still_cleans_runtime(tmp_path, monkeypatch):
    import asyncio

    from uwb_lab.experiments import BatchInput

    module = importlib.import_module("uwb_lab.app")
    runtime = module.Runtime(data_dir=tmp_path)
    runtime.source = "demo"
    monkeypatch.setattr(module, "runtime", runtime)

    async def scenario():
        async with module.lifespan(module.app):
            prior = runtime.state.set_prior({"mode": "distance", "distance_m": 2.0})
            runtime.experiments.start(BatchInput(), source="demo", prior=prior, radio_snapshot=None)

            def fail_manifest():
                raise OSError("shutdown disk error")

            monkeypatch.setattr(runtime.experiments, "_persist", fail_manifest)
        assert runtime.task.done()
        assert runtime.analysis.closing
        assert runtime.experiments.status()["outcome"] == "failed"

    asyncio.run(scenario())
