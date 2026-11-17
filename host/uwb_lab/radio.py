from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Literal, Protocol, TypeVar

import serial
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .core import SessionRecorder, utc_now


class ControlError(Exception):
    pass


T = TypeVar("T")


async def _settle(task: asyncio.Task[T]) -> tuple[T, bool]:
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


# DW3000 TX_POWER register: one byte per frame section (STS, SHR, PHR, data); in each byte bits 1:0 are
# the coarse gain and bits 7:2 the fine gain. These bytes come from the driver's channel-9 linear
# table (dwt_txp_lut_p0_b7_c9, 0.25 dB per index, index 0 = maximum) at indices 0, 12, ..., 72, so the
# presets are nominally 3 dB apart from 0 to -18 dB relative to the maximum. They are relative
# steps, not calibrated dBm.
TX_POWER_PRESETS = tuple(int(f"{byte:02X}" * 4, 16) for byte in (0xFE, 0x9E, 0x85, 0x4A, 0x3D, 0x48, 0x30))


class RadioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sampling_hz: float = Field(ge=0.1, le=50, allow_inf_nan=False)
    channel: Literal[5, 9]
    preamble: Literal[64, 128, 256, 512, 1024]
    data_rate: Literal[850, 6800]
    tx_power: int = Field(ge=0, le=0xFFFFFFFF, strict=True)

    @field_validator("tx_power")
    @classmethod
    def preset_power(cls, value: int) -> int:
        if value not in TX_POWER_PRESETS:
            raise ValueError("TX power must be one of the preset register values")
        return value

    @field_validator("channel", "preamble", "data_rate", mode="before")
    @classmethod
    def integer_selection(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("Radio selections must be integers")
        return value

    @property
    def period_ms(self) -> int:
        return round(1000 / self.sampling_hz)

    def device_values(self) -> tuple[int, ...]:
        return self.period_ms, self.channel, self.preamble, self.data_rate, self.tx_power


class BoardState(BaseModel):
    model_config = ConfigDict(strict=True)

    board: int = Field(ge=0, le=255)
    role: Literal["tx", "rx"]
    revision: int = Field(ge=0, le=0xFFFFFFFF)
    period_ms: int = Field(ge=20, le=10000)
    channel: Literal[5, 9]
    preamble: Literal[64, 128, 256, 512, 1024]
    data_rate: Literal[850, 6800]
    tx_power: int = Field(ge=0, le=0xFFFFFFFF)
    running: bool
    tx_ant_dly: int | None = Field(default=None, ge=0, le=65535)
    rx_ant_dly: int | None = Field(default=None, ge=0, le=65535)


class ControlReply(BoardState):
    type: Literal["control"]
    v: Literal[1]
    id: int = Field(ge=0, le=0xFFFFFFFF)
    ok: bool
    error: str = ""


class Board(Protocol):
    connected: bool

    async def command(self, action: str, *args: int) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...

    async def run(self, ingest: Callable[[str, float], Awaitable[None]]) -> None: ...


class SerialBoard:
    def __init__(self, port: str, role: Literal["tx", "rx"], timeout: float = 3) -> None:
        self.port = port
        self.role = role
        self.timeout = timeout
        self.connection: serial.Serial | None = None
        self.connected = False
        self.snapshot: dict[str, Any] | None = None
        self.error: str | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.lock = asyncio.Lock()
        self.next_id = secrets.randbelow(0x7FFFFFFF) + 1
        self.telemetry_dropped = 0
        self.connection_id: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "state": self.snapshot,
            "error": self.error,
            "telemetry_dropped": self.telemetry_dropped,
            "connection_id": self.connection_id,
        }

    def handle_line(self, line: str) -> bool:
        if not line.lstrip().startswith("{"):
            return False
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(message, dict) or message.get("type") != "control":
            return False
        try:
            reply = ControlReply.model_validate(message)
        except ValidationError as error:
            self.error = f"{self.role}: invalid control reply: {error}"
            return True
        future = self.pending.get(reply.id)
        if future is None or future.done():
            return True
        if reply.role != self.role:
            future.set_exception(ControlError(f"Expected {self.role} board, received {reply.role}"))
            return True
        self.snapshot = BoardState.model_validate(message).model_dump()
        if reply.ok:
            future.set_result(dict(self.snapshot))
        else:
            future.set_exception(ControlError(f"{self.role}: {reply.error or 'command rejected'}"))
        return True

    async def command(self, action: str, *args: int) -> dict[str, Any]:
        async with self.lock:
            connection = self.connection
            if connection is None or not self.connected:
                raise ControlError(f"{self.role}: USB board disconnected")
            self.next_id = (self.next_id % 0xFFFFFFFF) + 1
            identifier = self.next_id
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self.pending[identifier] = future
            wire = f"UWB {identifier} {action}"
            if args:
                wire += " " + " ".join(str(value) for value in args)
            data = (wire + "\n").encode("ascii")
            try:
                # A thread keeps writing after cancellation; retain the lock until it settles.
                count, cancelled = await _settle(
                    asyncio.create_task(asyncio.to_thread(connection.write, data))
                )
                if cancelled:
                    self.snapshot = None
                    raise asyncio.CancelledError
                if count != len(data):
                    raise ControlError(f"{self.role}: incomplete USB command write")
                result = await asyncio.wait_for(future, self.timeout)
                self.error = None
                return result
            except TimeoutError as error:
                self.snapshot = None
                self.error = f"{self.role}: acknowledgement timeout; board state unknown"
                raise ControlError(self.error) from error
            except (OSError, serial.SerialException) as error:
                self.snapshot = None
                self.error = f"{self.role}: {error}"
                raise ControlError(self.error) from error
            finally:
                self.pending.pop(identifier, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    async def _read(self, ingest: Callable[[str, float], Awaitable[None]]) -> None:
        connection = self.connection
        if connection is None:
            raise ControlError(f"{self.role}: USB board disconnected")
        telemetry: asyncio.Queue[tuple[str, float]] = asyncio.Queue(maxsize=300)

        async def deliver() -> None:
            while True:
                text, received_at = await telemetry.get()
                await ingest(text, received_at)
                telemetry.task_done()

        delivery = asyncio.create_task(deliver())
        buffer = bytearray()
        try:
            while True:
                if delivery.done():
                    await delivery
                    raise ControlError(f"{self.role}: telemetry delivery stopped")
                data = await asyncio.to_thread(connection.read, 256)
                buffer.extend(data)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    if len(line) > 2048:
                        raise ControlError(f"{self.role}: oversized USB line")
                    text = line.decode("utf-8", errors="replace")
                    if not self.handle_line(text) and self.role == "tx":
                        try:
                            telemetry.put_nowait((text, time.monotonic()))
                        except asyncio.QueueFull as error:
                            self.telemetry_dropped += 1
                            raise ControlError(
                                f"{self.role}: telemetry backlog exceeded 300 lines"
                            ) from error
                if len(buffer) > 2048:
                    raise ControlError(f"{self.role}: oversized unterminated USB line")
        finally:
            self.telemetry_dropped += telemetry.qsize()
            delivery.cancel()
            with suppress(asyncio.CancelledError):
                await delivery

    async def run(
        self,
        ingest: Callable[[str, float], Awaitable[None]],
        *,
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        while True:
            reader: asyncio.Task[None] | None = None
            try:
                self.connection = await asyncio.to_thread(
                    serial.Serial, self.port, baudrate=115200, timeout=0.1, write_timeout=1, exclusive=True
                )
                self.connected = True
                self.connection_id = secrets.token_hex(16)
                self.error = None
                reader = asyncio.create_task(self._read(ingest))
                await self.command("GET")
                await reader
            except (OSError, serial.SerialException, ControlError) as error:
                self.error = f"{self.role}: {error}"
            finally:
                self.connected = False
                if on_disconnect is not None:
                    on_disconnect(self.error or f"{self.role}: USB reader disconnected")
                self.snapshot = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ControlError(f"{self.role}: USB reader disconnected"))
                if reader is not None:
                    reader.cancel()
                    with suppress(asyncio.CancelledError, OSError, serial.SerialException, ControlError):
                        await reader
                if self.connection is not None:
                    await asyncio.to_thread(self.connection.close)
                    self.connection = None
            await asyncio.sleep(2)


DEVICE_FIELDS = ("period_ms", "channel", "preamble", "data_rate", "tx_power")
DELAY_FIELDS = ("tx_ant_dly", "rx_ant_dly")


def state_fields(state: dict[str, Any]) -> tuple[str, ...]:
    return (
        DEVICE_FIELDS + DELAY_FIELDS
        if all(state.get(key) is not None for key in DELAY_FIELDS)
        else DEVICE_FIELDS
    )


class RadioControl:
    def __init__(self, tx: Board, rx: Board, recorder: SessionRecorder | None = None) -> None:
        self.boards = {"tx": tx, "rx": rx}
        self.recorder = recorder
        self.lock = asyncio.Lock()
        self.busy = False
        self.last_error: str | None = None

    def status(self) -> dict[str, Any]:
        boards = {role: board.status() for role, board in self.boards.items()}
        states = [board["state"] for board in boards.values()]
        ready = (
            all(board["connected"] for board in boards.values())
            and all(state is not None for state in states)
            and states[0]["role"] == "tx"
            and states[1]["role"] == "rx"
            and states[0]["board"] != states[1]["board"]
            and all(states[0][key] == states[1][key] for key in DEVICE_FIELDS)
            and all(state["running"] for state in states)
            and self.last_error is None
        )
        return {
            "supported": True,
            "ready": bool(ready),
            "busy": self.busy,
            "boards": boards,
            "last_error": self.last_error,
        }

    def record(self, event: str, error: str | None = None) -> None:
        if self.recorder is not None:
            self.recorder.record_radio(
                {"event": event, "host_time": utc_now(), "boards": self.status()["boards"], "error": error}
            )

    async def _query(self) -> dict[str, dict[str, Any]]:
        states = {}
        for role, board in self.boards.items():
            if not board.connected:
                raise ControlError(f"{role}: USB board disconnected")
            states[role] = await board.command("GET")
            if states[role]["role"] != role:
                raise ControlError(f"Expected {role} board, received {states[role]['role']}")
        if states["tx"]["board"] == states["rx"]["board"]:
            raise ControlError("The two boards must have distinct board IDs")
        return states

    async def refresh(self) -> None:
        async with self.lock:
            await self._query()

    async def _pause(self) -> None:
        for role, board in self.boards.items():
            state = await board.command("PAUSE")
            if state["running"]:
                raise ControlError(f"{role}: board did not pause")

    async def _verify(self, expected: dict[str, dict[str, Any]]) -> None:
        actual = await self._query()
        for role, state in actual.items():
            if state["running"] or any(
                state.get(key) != expected[role][key] for key in (*state_fields(expected[role]), "revision")
            ):
                raise ControlError(f"{role}: configuration readback mismatch")

    async def _resume(self, expected: dict[str, dict[str, Any]]) -> None:
        # The responder must listen before the initiator can start polling.
        for role in ("rx", "tx"):
            state = await self.boards[role].command("RESUME")
            if not state["running"] or any(
                state.get(key) != expected[role][key] for key in (*state_fields(expected[role]), "revision")
            ):
                raise ControlError(f"{role}: resume readback mismatch")

    async def _recover(self, previous: dict[str, dict[str, Any]], revision: int) -> str:
        errors = []
        restored = {}
        # Try BOTH pauses even if one acknowledgement is lost.
        for role, board in self.boards.items():
            try:
                state = await board.command("PAUSE")
                if state["running"]:
                    raise ControlError("pause rejected")
                target = previous[role] | {"revision": revision}
                await board.command("SET", revision, *(target[key] for key in state_fields(target)))
                actual = await board.command("GET")
                if actual["running"] or any(
                    actual.get(key) != target[key] for key in (*state_fields(target), "revision")
                ):
                    raise ControlError("rollback readback mismatch")
                restored[role] = target
            except ControlError as error:
                errors.append(f"{role}: {error}")
        if len(restored) == 2:
            try:
                compatible = all(previous["tx"][key] == previous["rx"][key] for key in DEVICE_FIELDS)
                if compatible and all(state["running"] for state in previous.values()):
                    await self._resume(restored)
                    return "rollback confirmed; original settings restored"
                return "rollback confirmed; boards remain paused"
            except ControlError as error:
                errors.append(str(error))
        # A failed resume may have restarted one board. Stop both again.
        for role, board in self.boards.items():
            try:
                await board.command("PAUSE")
            except ControlError as error:
                errors.append(f"{role}: {error}")
            try:
                await board.command("GET")
            except ControlError as error:
                errors.append(f"{role}: state unknown: {error}")
        return "rollback failed; reachable boards left paused; " + "; ".join(errors)

    async def apply(self, config: RadioConfig) -> dict[str, Any]:
        return await self._apply(config)

    async def apply_calibration(self, delta_dtu: int, baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if type(delta_dtu) is not int or delta_dtu == 0:
            raise ControlError("Calibration adjustment must be a nonzero integer")
        return await self._apply(None, delta_dtu=delta_dtu, baseline=baseline)

    async def _apply(
        self,
        config: RadioConfig | None,
        *,
        delta_dtu: int = 0,
        baseline: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self.busy:
            raise ControlError("Another radio operation is in progress")
        async with self.lock:
            self.busy = True
            previous = None
            revision = 0
            changing = False
            try:
                previous = await self._query()
                if baseline is not None:
                    if previous != baseline:
                        raise ControlError("Board settings changed; capture a new calibration")
                    if not all(state["running"] for state in previous.values()) or not all(
                        previous["tx"][key] == previous["rx"][key] for key in DEVICE_FIELDS
                    ):
                        raise ControlError("Both boards must be running with compatible settings")
                expected = {}
                for role, state in previous.items():
                    target = dict(state)
                    if config is not None:
                        target.update(zip(DEVICE_FIELDS, config.device_values(), strict=True))
                    else:
                        for key in DELAY_FIELDS:
                            if state.get(key) is None:
                                raise ControlError("Calibration requires updated firmware on both boards")
                            value = state[key] + delta_dtu
                            if not 0 <= value <= 65535:
                                raise ControlError("Proposed antenna delay is outside the 0-65535 range")
                            target[key] = value
                    expected[role] = target
                revision = max(state["revision"] for state in previous.values()) + 1
                if revision >= 0xFFFFFFFF:
                    raise ControlError("Radio revision exhausted; restart both boards")
                self.record("before")
                changing = True
                await self._pause()
                for role, board in self.boards.items():
                    expected[role]["revision"] = revision
                    target = expected[role]
                    await board.command("SET", revision, *(target[key] for key in state_fields(target)))
                await self._verify(expected)
                await self._resume(expected)
                self.last_error = None
                self.record("applied")
                self.busy = False
                return self.status()
            except asyncio.CancelledError:
                recovery = (
                    (await _settle(asyncio.create_task(self._recover(previous, revision + 1))))[0]
                    if previous and changing
                    else "no changes sent"
                )
                self.last_error = f"Radio operation cancelled; {recovery}"
                self.record("cancelled", self.last_error)
                raise
            except ControlError as error:
                recovery, cancelled = (
                    await _settle(asyncio.create_task(self._recover(previous, revision + 1)))
                    if previous and changing
                    else ("no changes sent", False)
                )
                self.last_error = f"{error}; {recovery}"
                self.record("failed", self.last_error)
                if cancelled:
                    raise asyncio.CancelledError from error
                raise ControlError(self.last_error) from error
            finally:
                self.busy = False


class SampleRate:
    def __init__(self) -> None:
        self.times: deque[float] = deque(maxlen=100)

    def add(self, timestamp: float | None = None) -> None:
        self.times.append(time.monotonic() if timestamp is None else timestamp)

    def value(self, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        recent = [stamp for stamp in self.times if now - stamp <= 30]
        if len(recent) < 2:
            return 0.0 if self.times and now - self.times[-1] > 30 else None
        span = recent[-1] - recent[0]
        if span <= 0:
            return None
        rate = (len(recent) - 1) / span
        return 0.0 if now - recent[-1] > max(3, 3 / rate) else rate


class ExchangeQuality:
    """Share of recent ranging attempts that produced a sample, from the firmware exchange counter."""

    def __init__(self) -> None:
        self.recent: deque[tuple[float, int]] = deque(maxlen=100)

    def add(self, sequence: int, timestamp: float | None = None) -> None:
        if self.recent and sequence <= self.recent[-1][1]:
            self.recent.clear()  # the TX board restarted its counter
        self.recent.append((time.monotonic() if timestamp is None else timestamp, sequence))

    def success_pct(self, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        recent = [item for item in self.recent if now - item[0] <= 30]
        if len(recent) < 2:
            return None
        return 100 * len(recent) / (recent[-1][1] - recent[0][1] + 1)
