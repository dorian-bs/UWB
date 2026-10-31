from __future__ import annotations

import asyncio
import json
import os
import select
import threading
import time
import tty
from pathlib import Path

import pytest
from pydantic import ValidationError

from uwb_lab.core import SessionRecorder, parse_range_line


def radio_module():
    from uwb_lab import radio

    return radio


def config(**changes):
    return radio_module().RadioConfig(
        sampling_hz=5, channel=5, preamble=256, data_rate=850, tx_power=0x10101010, **changes
    )


class BoardSimulator:
    """Protocol-level board, including partial changes and lost acknowledgements."""

    def __init__(self, role, fail=None):
        self.role = role
        self.snapshot = {
            "board": 1 if role == "tx" else 2,
            "role": role,
            "revision": 0,
            "period_ms": 1000,
            "channel": 9,
            "preamble": 128,
            "data_rate": 6800,
            "tx_power": 0xA0A0A0A0,
            "running": True,
        }
        self.connected = True
        self.fail = fail
        self.commands = []
        self.fail_rollback = False

    async def command(self, action, *args):
        self.commands.append((action, args))
        if action == "PAUSE":
            self.snapshot["running"] = False
        elif action == "RESUME":
            self.snapshot["running"] = True
        elif action == "SET":
            keys = ("revision", "period_ms", "channel", "preamble", "data_rate", "tx_power")
            self.snapshot.update(zip(keys, args, strict=True))
        if self.fail == action or (self.fail_rollback and action == "SET"):
            self.fail = None
            raise radio_module().ControlError(f"{self.role}: acknowledgement timeout")
        return dict(self.snapshot)

    def status(self):
        return {"connected": self.connected, "state": dict(self.snapshot), "error": None}


def pair(fail=None, recorder=None):
    radio = radio_module()
    tx, rx = BoardSimulator("tx"), BoardSimulator("rx", fail)
    control = radio.RadioControl(tx, rx, recorder)
    return control, tx, rx


def test_radio_validation_and_period_quantization():
    radio = radio_module()
    assert config().period_ms == 200
    assert (
        radio.RadioConfig(sampling_hz=3, channel=9, preamble=128, data_rate=6800, tx_power=0).period_ms == 333
    )
    for field, value in [
        ("sampling_hz", 0),
        ("sampling_hz", 10.01),
        ("sampling_hz", float("nan")),
        ("channel", 7),
        ("preamble", 65),
        ("data_rate", 110),
        ("tx_power", -1),
        ("tx_power", 2**32),
        ("tx_power", 1.2),
        ("channel", True),
    ]:
        payload = config().model_dump() | {field: value}
        with pytest.raises(ValidationError):
            radio.RadioConfig(**payload)


def test_pair_applies_only_after_pausing_both_and_keeps_history():
    async def scenario():
        control, tx, rx = pair()
        result = await control.apply(config())
        assert result["ready"]
        for board in (tx, rx):
            assert [action for action, _ in board.commands] == ["GET", "PAUSE", "SET", "GET", "RESUME"]
            assert board.snapshot["period_ms"] == 200
            assert board.snapshot["channel"] == 5
            assert board.snapshot["running"]
            assert board.snapshot["revision"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_action", ["PAUSE", "SET", "RESUME"])
def test_lost_ack_rolls_back_both_radios_before_resuming(failed_action):
    async def scenario():
        control, tx, rx = pair(failed_action)
        before = dict(tx.snapshot)
        with pytest.raises(radio_module().ControlError, match="rollback"):
            await control.apply(config())
        for board in (tx, rx):
            assert board.snapshot["channel"] == before["channel"]
            assert board.snapshot["period_ms"] == before["period_ms"]
            assert board.snapshot["running"]
        assert control.status()["last_error"]

    asyncio.run(scenario())


def test_failed_rollback_leaves_both_paused_with_visible_error():
    async def scenario():
        control, tx, rx = pair("SET")
        rx.fail_rollback = True
        with pytest.raises(radio_module().ControlError, match="paused"):
            await control.apply(config())
        assert not tx.snapshot["running"]
        assert not rx.snapshot["running"]
        assert not control.status()["ready"]

    asyncio.run(scenario())


def test_disconnected_or_wrong_role_pair_does_not_change_settings():
    async def scenario():
        control, tx, rx = pair()
        rx.connected = False
        with pytest.raises(radio_module().ControlError):
            await control.apply(config())
        assert not any(action == "SET" for action, _ in tx.commands)
        rx.connected = True
        rx.snapshot["role"] = "tx"
        with pytest.raises(radio_module().ControlError):
            await control.apply(config())

    asyncio.run(scenario())


def test_radio_configuration_recording_and_telemetry_revision(tmp_path: Path):
    async def scenario():
        recorder = SessionRecorder(tmp_path)
        directory = recorder.start("radio", "dual-serial")
        control, _, _ = pair(recorder=recorder)
        await control.apply(config())
        recorder.stop()
        events = [json.loads(line) for line in (directory / "radio.ndjson").read_text().splitlines()]
        assert events[0]["event"] == "before"
        assert events[-1]["event"] == "applied"
        assert events[-1]["boards"]["tx"]["state"]["revision"] == 1

    asyncio.run(scenario())
    line = {
        "type": "range",
        "v": 1,
        "board": 1,
        "peer": 2,
        "seq": 1,
        "time_ms": 10,
        "raw_m": 2,
        "corrected_m": 2,
        "valid": True,
        "radio_revision": 7,
    }
    assert parse_range_line(json.dumps(line))["radio_revision"] == 7
    line["radio_revision"] = -1
    assert parse_range_line(json.dumps(line)) is None


def test_radio_endpoint_rejects_non_hardware_source():
    from fastapi.testclient import TestClient

    from uwb_lab import app as app_module

    with TestClient(app_module.app) as client:
        response = client.post("/api/radio", json=config().model_dump())
        assert response.status_code == 409
        assert client.get("/api/status").json()["radio"]["supported"] is False


def test_radio_endpoint_rejects_invalid_settings_before_commands():
    from fastapi.testclient import TestClient

    from uwb_lab import app as app_module

    with TestClient(app_module.app) as client:
        response = client.post("/api/radio", json=config().model_dump() | {"sampling_hz": 100})
        assert response.status_code == 422


def test_serial_ack_requires_matching_id_and_valid_board_state():
    async def scenario():
        radio = radio_module()
        board = radio.SerialBoard("unused", "tx")
        future = asyncio.get_running_loop().create_future()
        board.pending[42] = future
        state = BoardSimulator("tx").snapshot
        assert board.handle_line(json.dumps({"type": "control", "v": 1, "id": 43, "ok": True, **state}))
        assert not future.done()
        board.handle_line(json.dumps({"type": "control", "v": 1, "id": 42, "ok": True, **state}))
        assert future.result()["board"] == 1
        assert board.snapshot["channel"] == 9

    asyncio.run(scenario())


def test_observed_rate_uses_recent_arrival_times_and_becomes_stale():
    radio = radio_module()
    rate = radio.SampleRate()
    assert rate.value(0) is None
    for timestamp in (0, 0.2, 0.4, 0.6, 0.8):
        rate.add(timestamp)
    assert rate.value(0.8) == pytest.approx(5)
    assert rate.value(20) == 0


def test_revision_exhaustion_does_not_send_mutating_commands():
    async def scenario():
        control, tx, rx = pair()
        tx.snapshot["revision"] = rx.snapshot["revision"] = 0xFFFFFFFE
        with pytest.raises(radio_module().ControlError, match="exhausted"):
            await control.apply(config())
        assert [action for action, _ in tx.commands] == ["GET"]
        assert [action for action, _ in rx.commands] == ["GET"]

    asyncio.run(scenario())


def test_replay_preserves_radio_revision(monkeypatch, tmp_path):
    from uwb_lab import app as app_module

    line = {
        "type": "range",
        "v": 1,
        "board": 1,
        "peer": 2,
        "seq": 1,
        "time_ms": 200,
        "raw_m": 2,
        "corrected_m": 2,
        "valid": True,
        "radio_revision": 3,
    }
    path = tmp_path / "replay.ndjson"
    path.write_text(json.dumps(line) + "\n")
    monkeypatch.setenv("UWB_REPLAY_FILE", str(path))

    async def scenario():
        source = app_module.replay_source()
        try:
            assert json.loads(await anext(source))["radio_revision"] == 3
        finally:
            await source.aclose()

    asyncio.run(scenario())


def test_replay_preserves_v2_diagnostics(monkeypatch, tmp_path):
    from uwb_lab import app as app_module

    line = {
        "type": "range",
        "v": 2,
        "board": 1,
        "peer": 2,
        "seq": 1,
        "time_ms": 200,
        "raw_m": 2,
        "corrected_m": 2,
        "valid": True,
        "ipatov_power": 123,
        "ipatov_f1": 10,
        "ipatov_f2": 11,
        "ipatov_f3": 12,
        "ipatov_fp_index": 256,
        "ipatov_accum_count": 64,
        "cia_diag1": 99,
    }
    path = tmp_path / "replay.ndjson"
    path.write_text(json.dumps(line) + "\n")
    monkeypatch.setenv("UWB_REPLAY_FILE", str(path))

    async def scenario():
        source = app_module.replay_source()
        try:
            sample = json.loads(await anext(source))
            assert sample["ipatov_power"] == 123
            assert sample["cia_diag1"] == 99
        finally:
            await source.aclose()

    asyncio.run(scenario())


def test_configuration_readback_mismatch_is_not_success():
    async def scenario():
        control, tx, rx = pair()
        original = rx.command
        reads = 0

        async def wrong_readback(action, *args):
            nonlocal reads
            result = await original(action, *args)
            if action == "GET":
                reads += 1
                if reads == 2:
                    result["channel"] = 9
            return result

        rx.command = wrong_readback
        with pytest.raises(radio_module().ControlError, match="readback mismatch"):
            await control.apply(config())
        assert tx.snapshot["channel"] == rx.snapshot["channel"] == 9

    asyncio.run(scenario())


def test_cancelled_configuration_restores_original_settings():
    async def scenario():
        control, tx, rx = pair()
        entered = asyncio.Event()
        original = rx.command

        async def delayed_set(action, *args):
            if action == "SET" and args[0] == 1:
                entered.set()
                await asyncio.Event().wait()
            return await original(action, *args)

        rx.command = delayed_set
        task = asyncio.create_task(control.apply(config()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert tx.snapshot["channel"] == rx.snapshot["channel"] == 9
        assert tx.snapshot["running"] and rx.snapshot["running"]
        assert "cancelled" in control.status()["last_error"]

    asyncio.run(scenario())


def test_cancelled_serial_write_settles_before_releasing_command_lock():
    async def scenario():
        radio = radio_module()
        board = radio.SerialBoard("unused", "tx")
        entered, release = threading.Event(), threading.Event()

        class BlockingConnection:
            def write(self, data):
                entered.set()
                assert release.wait(2)
                return len(data)

        board.connection = BlockingConnection()
        board.connected = True
        task = asyncio.create_task(board.command("PAUSE"))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0.03)
            assert not task.done(), "Cancellation must not abandon an in-flight physical write"
            assert board.lock.locked()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert not board.lock.locked()
        assert task.cancelled()

    asyncio.run(scenario())


def test_cancellation_during_error_recovery_keeps_transaction_locked():
    async def scenario():
        radio = radio_module()
        control, tx, rx = pair()
        tx.fail = "RESUME"
        entered, release = asyncio.Event(), asyncio.Event()
        original = rx.command
        pauses = 0

        async def delayed_recovery(action, *args):
            nonlocal pauses
            if action == "PAUSE":
                pauses += 1
                if pauses == 2:
                    entered.set()
                    await release.wait()
            return await original(action, *args)

        rx.command = delayed_recovery
        task = asyncio.create_task(control.apply(config()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.sleep(0.03)
            assert not task.done()
            assert control.lock.locked() and control.busy
            with pytest.raises(radio.ControlError, match="in progress"):
                await control.apply(config())
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert tx.snapshot["channel"] == rx.snapshot["channel"] == 9
        assert tx.snapshot["running"] and rx.snapshot["running"]
        assert control.status()["last_error"]

    asyncio.run(scenario())


def test_disconnected_peer_does_not_prevent_reachable_board_restoration():
    async def scenario():
        control, tx, rx = pair()
        original = rx.command

        async def disconnect_on_set(action, *args):
            if action == "SET":
                rx.connected = False
            if not rx.connected:
                raise radio_module().ControlError("rx disconnected")
            return await original(action, *args)

        rx.command = disconnect_on_set
        with pytest.raises(radio_module().ControlError, match="paused"):
            await control.apply(config())
        assert tx.snapshot["channel"] == 9
        assert tx.snapshot["revision"] == 2
        assert not tx.snapshot["running"]

    asyncio.run(scenario())


def test_serial_reader_dispatches_ack_while_telemetry_delivery_is_blocked():
    async def scenario():
        radio = radio_module()
        board = radio.SerialBoard("unused", "tx", timeout=0.2)
        state = BoardSimulator("tx").snapshot
        future = asyncio.get_running_loop().create_future()
        board.pending[42] = future
        telemetry = b'{"type":"range"}\n'
        reply = (json.dumps({"type": "control", "v": 1, "id": 42, "ok": True, **state}) + "\n").encode()
        chunks = [telemetry, reply]
        entered = asyncio.Event()

        class Connection:
            def read(self, count):
                if chunks:
                    return chunks.pop(0)
                time.sleep(0.01)
                return b""

        async def blocked_ingest(line, received_at):
            entered.set()
            await asyncio.Event().wait()

        board.connection = Connection()
        task = asyncio.create_task(board._read(blocked_ingest))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert (await asyncio.wait_for(future, 0.2))["board"] == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_slow_viewer_is_closed_without_stalling_recording(caplog):
    from uwb_lab import app as app_module

    async def scenario():
        class SlowViewer:
            closed = False

            async def send_json(self, message):
                await asyncio.Event().wait()

            async def close(self, code):
                self.closed = True

        runtime = app_module.Runtime()
        viewer = SlowViewer()
        runtime.clients.add(viewer)
        sample = {
            "type": "range",
            "v": 1,
            "board": 1,
            "peer": 2,
            "seq": 1,
            "time_ms": 100,
            "raw_m": 2,
            "corrected_m": 2,
            "valid": True,
        }
        await asyncio.wait_for(runtime.ingest(json.dumps(sample)), 1.2)
        assert viewer.closed
        assert viewer not in runtime.clients
        assert runtime.state.total_samples == 1

    asyncio.run(scenario())
    assert "telemetry delivery failed" in caplog.text


def test_telemetry_queue_overflow_is_explicit_and_counted():
    async def scenario():
        radio = radio_module()
        board = radio.SerialBoard("unused", "tx")
        entered = asyncio.Event()
        chunks = [b"first\n", b"sample\n" * 301]

        class Connection:
            def read(self, count):
                if chunks:
                    return chunks.pop(0)
                return b""

        async def blocked_ingest(line, received_at):
            entered.set()
            await asyncio.Event().wait()

        board.connection = Connection()
        with pytest.raises(radio.ControlError, match="backlog exceeded"):
            await board._read(blocked_ingest)
        assert board.telemetry_dropped >= 301

    asyncio.run(scenario())


def test_buffered_telemetry_keeps_usb_arrival_times_for_observed_rate():
    async def scenario():
        radio = radio_module()
        board = radio.SerialBoard("unused", "tx")
        release = asyncio.Event()
        received = []
        all_delivered = asyncio.Event()
        chunks = [b"first\n", b"second\n", b"third\n"]
        reads = threading.Event()

        class Connection:
            def read(self, count):
                time.sleep(0.02)
                if chunks:
                    chunk = chunks.pop(0)
                    if not chunks:
                        reads.set()
                    return chunk
                return b""

        async def delayed_ingest(line, received_at=None):
            await release.wait()
            received.append(received_at)
            if len(received) == 3:
                all_delivered.set()

        board.connection = Connection()
        task = asyncio.create_task(board._read(delayed_ingest))
        try:
            assert await asyncio.to_thread(reads.wait, 1)
            release.set()
            await asyncio.wait_for(all_delivered.wait(), 1)
            assert all(isinstance(stamp, float) for stamp in received)
            assert received[-1] - received[0] >= 0.03
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


class USBEmulator:
    """Use real pyserial on an isolated pseudo-terminal, never a physical port."""

    def __init__(self, role):
        self.board = BoardSimulator(role)
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.port = os.ttyname(self.slave)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.serve)
        self.sequence = 0
        self.thread.start()

    def serve(self):
        buffer = b""
        while not self.stopped.is_set():
            if not select.select([self.master], [], [], 0.05)[0]:
                continue
            buffer += os.read(self.master, 256)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                tokens = line.decode().split()
                assert tokens[0] == "UWB"
                identifier, action = int(tokens[1]), tokens[2]
                args = [int(token) for token in tokens[3:]]
                state = asyncio.run(self.board.command(action, *args))
                reply = json.dumps({"type": "control", "v": 1, "id": identifier, "ok": True, **state})
                wire = (reply + "\n").encode()
                os.write(self.master, wire[:30])
                os.write(self.master, wire[30:])
                if action == "RESUME" and state["role"] == "tx":
                    self.sequence += 1
                    sample = {
                        "type": "range",
                        "v": 1,
                        "board": 1,
                        "peer": 2,
                        "seq": self.sequence,
                        "time_ms": self.sequence * state["period_ms"],
                        "raw_m": 2.12,
                        "corrected_m": 2.12,
                        "valid": True,
                        "radio_revision": state["revision"],
                    }
                    os.write(self.master, (json.dumps(sample) + "\n").encode())

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()
        os.close(self.master)
        os.close(self.slave)


def test_dual_usb_http_apply_records_revisioned_samples(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from uwb_lab import app as app_module

    tx, rx = USBEmulator("tx"), USBEmulator("rx")
    try:
        monkeypatch.setenv("UWB_SOURCE", "dual-serial")
        monkeypatch.setenv("UWB_SERIAL_PORT", tx.port)
        monkeypatch.setenv("UWB_RESPONDER_PORT", rx.port)
        runtime = app_module.Runtime()
        runtime.recorder = SessionRecorder(tmp_path)
        runtime.radio.recorder = runtime.recorder
        monkeypatch.setattr(app_module, "runtime", runtime)
        with TestClient(app_module.app) as client:
            deadline = time.monotonic() + 5
            while not client.get("/api/status").json()["radio"]["ready"]:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            started = client.post("/api/recording/start", json={"name": "USB"}).json()
            client.post("/api/prior", json={"distance_m": 2})
            applied = client.post("/api/radio", json=config().model_dump())
            assert applied.status_code == 200, applied.text
            assert not applied.json()["busy"]
            deadline = time.monotonic() + 5
            while not client.get("/api/samples").json()["samples"]:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            sample = client.get("/api/samples").json()["samples"][-1]
            assert sample["radio_revision"] == 1
            assert sample["residual_m"] == pytest.approx(0.12)
            assert client.get("/api/status").json()["malformed_lines"] == 0
            client.post("/api/recording/stop")
            directory = tmp_path / started["session"]
            telemetry = json.loads((directory / "telemetry.ndjson").read_text().splitlines()[-1])
            assert telemetry["radio_revision"] == 1
    finally:
        tx.close()
        rx.close()
