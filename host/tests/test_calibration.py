from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
from test_radio import BoardSimulator, USBEmulator, config, radio_module


class CalibratableBoard(BoardSimulator):
    def __init__(self, role, fail=None):
        super().__init__(role, fail)
        self.snapshot.update(tx_ant_dly=16380, rx_ant_dly=16380)

    async def command(self, action, *args):
        if action == "SET" and len(args) == 8:
            self.snapshot.update(tx_ant_dly=args[6], rx_ant_dly=args[7])
            args = args[:6]
        return await super().command(action, *args)


def pair(fail=None):
    tx, rx = CalibratableBoard("tx"), CalibratableBoard("rx", fail)
    return radio_module().RadioControl(tx, rx), tx, rx


def test_apply_delays_preserves_radio_and_subsequent_radio_apply():
    async def scenario():
        control, tx, rx = pair()
        baseline = {role: dict(board.snapshot) for role, board in control.boards.items()}
        await control.apply_calibration(10, baseline)
        for board in (tx, rx):
            assert board.snapshot["tx_ant_dly"] == 16390
            assert board.snapshot["rx_ant_dly"] == 16390
            assert board.snapshot["channel"] == 9
            assert board.snapshot["revision"] == 1
        await control.apply(config())
        assert tx.snapshot["tx_ant_dly"] == 16390
        assert rx.snapshot["rx_ant_dly"] == 16390

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["SET", "RESUME"])
def test_calibration_lost_ack_restores_delays(failure):
    async def scenario():
        control, tx, rx = pair(failure)
        baseline = {role: dict(board.snapshot) for role, board in control.boards.items()}
        with pytest.raises(radio_module().ControlError, match="rollback confirmed"):
            await control.apply_calibration(-20, baseline)
        for board in (tx, rx):
            assert board.snapshot["tx_ant_dly"] == board.snapshot["rx_ant_dly"] == 16380
            assert board.snapshot["running"]

    asyncio.run(scenario())


def test_stale_or_out_of_range_calibration_sends_no_changes():
    async def scenario():
        control, tx, rx = pair()
        baseline = {role: dict(board.snapshot) for role, board in control.boards.items()}
        tx.snapshot["revision"] += 1
        with pytest.raises(radio_module().ControlError, match="changed"):
            await control.apply_calibration(10, baseline)
        baseline["tx"]["revision"] += 1
        with pytest.raises(radio_module().ControlError, match="range"):
            await control.apply_calibration(-20000, baseline)
        assert all(action == "GET" for board in (tx, rx) for action, _ in board.commands)

    asyncio.run(scenario())


def service(tmp_path):
    from uwb_lab.calibration import CalibrationCapture, CalibrationInput

    return CalibrationCapture(tmp_path), CalibrationInput(known_distance_m=5, target_samples=3)


def sample(seq, value=5.093808, **changes):
    return {
        "board": 1,
        "peer": 2,
        "seq": seq,
        "time_ms": seq * 1000,
        "raw_m": value,
        "corrected_m": value,
        "valid": True,
        "radio_revision": 0,
        **changes,
    }


def test_capture_computes_fixed_conversion_and_audits_application(tmp_path):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, tx, _ = pair()
        started = await capture.start(control, inputs)
        for seq in range(1, 4):
            capture.observe(sample(seq), control)
        result = capture.status()
        assert result["phase"] == "ready"
        assert result["delta_dtu"] == 10
        assert result["proposed"]["tx"]["tx_ant_dly"] == 16390
        assert result["count"] == 3
        original = list(capture.samples)
        applied = await capture.apply(control, started["id"])
        assert applied["phase"] == "applied"
        assert capture.samples == original
        assert tx.snapshot["tx_ant_dly"] == 16390
        audit = json.loads((tmp_path / "calibrations" / f"{started['id']}.json").read_text())
        assert audit["phase"] == "applied"
        assert audit["samples"] == original
        assert audit["after"]["boards"]["tx"]["state"]["tx_ant_dly"] == 16390
        with pytest.raises(radio_module().ControlError):
            await capture.apply(control, started["id"])

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"radio_revision": 1}, "changed"),
        ({"peer": 3}, "identity"),
        ({"corrected_m": 5.01}, "correction"),
        ({"seq": 1, "time_ms": 1000}, "clock"),
    ],
)
def test_capture_rejects_mixed_or_reset_measurements(tmp_path, changes, reason):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, _, _ = pair()
        await capture.start(control, inputs)
        capture.observe(sample(1), control)
        capture.observe(sample(2) | changes, control)
        assert capture.status()["phase"] == "invalid"
        assert reason in capture.status()["error"]
        with pytest.raises(radio_module().ControlError):
            await capture.apply(control, capture.status()["id"])

    asyncio.run(scenario())


def test_api_capture_apply_and_configuration_locks(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from uwb_lab import app as app_module

    runtime = app_module.Runtime(tmp_path)
    control, tx, _ = pair()
    runtime.source = "dual-serial"
    runtime.radio = control
    runtime.connected = True
    monkeypatch.setattr(app_module, "runtime", runtime)
    # No lifespan: the test injects actual parsed telemetry, never starts a USB reader.
    client = TestClient(app_module.app)
    response = client.post("/api/calibration/start", json={"known_distance_m": 5, "target_samples": 3})
    assert response.status_code == 200
    identifier = response.json()["id"]
    assert client.post("/api/radio", json=config().model_dump()).status_code == 409
    assert client.post("/api/prior", json={"distance_m": 5}).status_code == 409
    assert client.post("/api/experiments/start", json={}).status_code == 409
    for seq in range(1, 4):
        asyncio.run(runtime.ingest(json.dumps({"type": "range", "v": 1, **sample(seq)})))
    assert client.get("/api/status").json()["calibration"]["phase"] == "ready"
    assert client.post("/api/calibration/apply", json={"id": identifier, "delta_dtu": 300}).status_code == 422
    response = client.post("/api/calibration/apply", json={"id": identifier})
    assert response.status_code == 200, response.text
    assert tx.snapshot["tx_ant_dly"] == 16390
    assert client.post("/api/calibration/apply", json={"id": identifier}).status_code == 409


@pytest.mark.parametrize("source", ["demo", "replay", "serial", "tcp"])
def test_api_rejects_non_dual_sources(tmp_path, monkeypatch, source):
    from fastapi.testclient import TestClient

    from uwb_lab import app as app_module

    runtime = app_module.Runtime(tmp_path)
    runtime.source = source
    monkeypatch.setattr(app_module, "runtime", runtime)
    client = TestClient(app_module.app)
    assert client.post("/api/calibration/start", json={"known_distance_m": 5}).status_code == 409
    assert client.post("/api/calibration/apply", json={"id": "x"}).status_code == 409


def test_capture_timeout_status_and_invalid_samples(tmp_path):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, _, _ = pair()
        await capture.start(control, inputs)
        capture.observe(sample(1, valid=False), control)
        assert capture.status()["count"] == 0
        assert capture.status()["skipped"] == 1
        capture.last_received -= 31
        status = capture.status()
        assert status["phase"] == "invalid"
        assert not status["active"]

    asyncio.run(scenario())


def test_capture_timeout_does_not_depend_on_browser_polling(tmp_path):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, _, _ = pair()
        await capture.start(control, inputs)
        capture.last_received -= 31
        capture.observe(sample(1), control)
        assert capture.status()["phase"] == "invalid"
        assert capture.status()["count"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("value,delta", [(4.906192, -10), (5.005, 0)])
def test_capture_negative_bias_and_tolerance(tmp_path, value, delta):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, _, _ = pair()
        await capture.start(control, inputs)
        capture.observe(sample(1), control, received_at=capture.started - 1)
        assert capture.status()["count"] == 0
        for seq in range(1, 4):
            capture.observe(sample(seq, value), control)
        assert capture.status()["delta_dtu"] == delta
        if delta == 0:
            with pytest.raises(radio_module().ControlError, match="No hardware change"):
                await capture.apply(control, capture.status()["id"])

    asyncio.run(scenario())


def test_equal_four_register_increment_matches_ds_twr_equation():
    from uwb_lab.calibration import METRES_PER_DTU

    def tof(t):
        ra, rb, da, db = t[3] - t[0], t[5] - t[2], t[2] - t[1], t[4] - t[3]
        return (ra * rb - da * db) / (ra + rb + da + db)

    # Unequal reply intervals: neither symmetry nor a single-board adjustment is assumed.
    stamps = [0, 1000, 1001000, 1002000, 2502000, 2503000]
    for delta in (-20, 1, 10):
        adjusted = [stamp + (delta if i in (0, 2, 4) else -delta) for i, stamp in enumerate(stamps)]
        change_m = (tof(stamps) - tof(adjusted)) * 299702547.0 / (499.2e6 * 128)
        assert change_m == pytest.approx(delta * METRES_PER_DTU)


def test_legacy_boards_and_disconnected_capture_cannot_apply(tmp_path):
    async def scenario():
        capture, inputs = service(tmp_path)
        old = radio_module().RadioControl(BoardSimulator("tx"), BoardSimulator("rx"))
        with pytest.raises(radio_module().ControlError, match="firmware"):
            await capture.start(old, inputs)
        control, _, _ = pair()
        await capture.start(control, inputs)
        for seq in range(1, 4):
            capture.observe(sample(seq), control)
        capture.invalidate("USB disconnected")
        with pytest.raises(radio_module().ControlError, match="disconnected"):
            await capture.apply(control, capture.status()["id"])

    asyncio.run(scenario())


def test_audit_failure_prevents_any_hardware_change(tmp_path, monkeypatch):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, tx, rx = pair()
        await capture.start(control, inputs)
        for seq in range(1, 4):
            capture.observe(sample(seq), control)

        def fail(*args):
            raise OSError("disk full")

        monkeypatch.setattr("uwb_lab.calibration.write_json_atomic", fail)
        with pytest.raises(OSError, match="disk full"):
            await capture.apply(control, capture.status()["id"])
        assert all(action == "GET" for board in (tx, rx) for action, _ in board.commands)

    asyncio.run(scenario())


def test_cancelled_calibration_restores_delays_and_saves_failed_audit(tmp_path):
    async def scenario():
        capture, inputs = service(tmp_path)
        control, tx, rx = pair()
        await capture.start(control, inputs)
        for seq in range(1, 4):
            capture.observe(sample(seq), control)
        entered = asyncio.Event()
        original = rx.command

        async def blocked(action, *args):
            if action == "SET" and args[0] == 1:
                entered.set()
                await asyncio.Event().wait()
            return await original(action, *args)

        rx.command = blocked
        task = asyncio.create_task(capture.apply(control, capture.status()["id"]))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not control.busy
        assert tx.snapshot["tx_ant_dly"] == rx.snapshot["rx_ant_dly"] == 16380
        assert tx.snapshot["running"] and rx.snapshot["running"]
        audit = json.loads(next((tmp_path / "calibrations").glob("*.json")).read_text())
        assert audit["phase"] == "failed"
        assert "cancelled" in audit["error"]
        assert audit["after"]["boards"]["tx"]["state"]["tx_ant_dly"] == 16380

    asyncio.run(scenario())


def test_delay_readback_mismatch_and_failed_rollback_leave_radios_paused():
    async def scenario():
        control, tx, rx = pair()
        baseline = {role: dict(board.snapshot) for role, board in control.boards.items()}
        original = rx.command

        async def wrong_delay(action, *args):
            state = await original(action, *args)
            if action == "GET" and state["revision"] > 0:
                state["rx_ant_dly"] += 1
            return state

        rx.command = wrong_delay
        with pytest.raises(radio_module().ControlError, match="rollback failed"):
            await control.apply_calibration(10, baseline)
        assert not tx.snapshot["running"] and not rx.snapshot["running"]
        assert not control.status()["ready"]

    asyncio.run(scenario())


def test_usb_capture_apply_uses_real_serial_commands_and_retains_audit(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from uwb_lab import app as app_module

    tx, rx = USBEmulator("tx"), USBEmulator("rx")
    tx.board, rx.board = CalibratableBoard("tx"), CalibratableBoard("rx")
    try:
        monkeypatch.setenv("UWB_SOURCE", "dual-serial")
        monkeypatch.setenv("UWB_SERIAL_PORT", tx.port)
        monkeypatch.setenv("UWB_RESPONDER_PORT", rx.port)
        runtime = app_module.Runtime(tmp_path)
        monkeypatch.setattr(app_module, "runtime", runtime)
        with TestClient(app_module.app) as client:

            def wait_for(predicate):
                deadline = time.monotonic() + 5
                while not predicate():
                    assert time.monotonic() < deadline
                    time.sleep(0.02)

            def connected_pair():
                status = client.get("/api/status").json()
                return status["connected"] and status["radio"]["ready"]

            wait_for(connected_pair)
            started = client.post("/api/calibration/start", json={"known_distance_m": 5, "target_samples": 3})
            assert started.status_code == 200, started.text
            for seq in range(1, 4):
                os.write(tx.master, (json.dumps({"type": "range", "v": 1, **sample(seq)}) + "\n").encode())
            wait_for(lambda: client.get("/api/status").json()["calibration"]["phase"] == "ready")
            response = client.post("/api/calibration/apply", json={"id": started.json()["id"]})
            assert response.status_code == 200, response.text
            for device in (tx, rx):
                assert device.board.snapshot["tx_ant_dly"] == device.board.snapshot["rx_ant_dly"] == 16390
            assert response.json()["after"]["boards"]["tx"]["state"]["revision"] == 1
            assert next((tmp_path / "calibrations").glob("*.json")).is_file()
    finally:
        tx.close()
        rx.close()
