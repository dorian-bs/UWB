"""Hardware-free dashboard smoke tests with isolated runtime and recording storage."""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest


def test_serves_ui_and_status(client) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]

    status = client.get("/api/status").json()
    assert status["source"] == "demo"
    assert status["recording"] is False
    assert status["prior"] is None


def test_prior_updates_and_rejects_bad_input(client) -> None:
    prior = client.post(
        "/api/prior",
        json={"mode": "positions", "node_a_m": [0, 0, 0], "node_b_m": [3, 4, 0]},
    ).json()
    assert prior["expected_distance_m"] == pytest.approx(5.0)
    assert client.post("/api/prior", json={"mode": "nonsense"}).status_code == 400


def test_recording_round_trip_writes_a_session(client, tmp_path) -> None:
    started = client.post("/api/recording/start", json={"name": "smoke test"}).json()
    assert started["recording"] is True

    with client.websocket_connect("/ws") as socket:
        assert socket.receive_json()["type"] == "snapshot"
        message = socket.receive_json()
        assert message["type"] == "sample"
        assert "raw_m" in message["sample"]

    stopped = client.post("/api/recording/stop").json()
    assert stopped["recording"] is False

    session = tmp_path / stopped["session"]
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed"] is True
    assert manifest["sample_count"] > 0


@pytest.mark.parametrize(
    ("source", "setting"),
    [("tcp", "UWB_TCP_HOST"), ("serial", "UWB_SERIAL_PORT")],
)
def test_missing_hardware_configuration_is_visible_in_status(tmp_path, monkeypatch, source, setting):
    module = importlib.import_module("uwb_lab.app")
    monkeypatch.setenv("UWB_SOURCE", source)
    monkeypatch.delenv(setting, raising=False)
    runtime = module.Runtime(data_dir=tmp_path)
    monkeypatch.setattr(module, "runtime", runtime)

    asyncio.run(module.acquisition_loop())

    assert runtime.status()["connected"] is False
    assert setting in runtime.status()["last_error"]
