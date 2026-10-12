from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .core import LabState, SessionRecorder

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("UWB_DATA_DIR", PACKAGE_DIR.parent.parent / "data" / "sessions"))


class PriorInput(BaseModel):
    mode: str = "distance"
    distance_m: float | None = None
    node_a_m: list[float] | None = None
    node_b_m: list[float] | None = None
    uncertainty_m: float = Field(default=0.0, ge=0)
    note: str = Field(default="", max_length=200)


class RecordingInput(BaseModel):
    name: str = Field(default="session", max_length=80)


class Runtime:
    def __init__(self) -> None:
        self.source = os.getenv("UWB_SOURCE", "demo")
        self.state = LabState()
        self.recorder = SessionRecorder(DATA_DIR)
        self.clients: set[WebSocket] = set()
        self.connected = False
        self.last_error: str | None = None
        self.task: asyncio.Task[None] | None = None

    async def ingest(self, line: str) -> None:
        sample = self.state.add_line(line)
        if sample is None:
            return
        self.recorder.record_sample(sample)
        stale_clients: list[WebSocket] = []
        for client in self.clients:
            try:
                await client.send_json({"type": "sample", "sample": sample})
            except Exception:
                stale_clients.append(client)
        for client in stale_clients:
            self.clients.discard(client)

    def status(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "connected": self.connected,
            "last_error": self.last_error,
            "total_samples": self.state.total_samples,
            "ignored_lines": self.state.ignored_lines,
            "malformed_lines": self.state.malformed_lines,
            "sequence_gaps": self.state.sequence_gaps,
            "recording": self.recorder.active,
            "session": self.recorder.session_dir.name if self.recorder.session_dir else None,
            "prior": self.state.prior.to_dict() if self.state.prior else None,
        }


runtime = Runtime()


async def demo_source() -> AsyncIterator[str]:
    sequence = 0
    started = time.monotonic()
    while True:
        sequence += 1
        elapsed = time.monotonic() - started
        true_distance = 2.0 + 0.25 * __import__("math").sin(elapsed / 8)
        measured = true_distance + random.gauss(0, 0.025)
        message = {
            "type": "range",
            "v": 1,
            "board": 1,
            "peer": 2,
            "seq": sequence,
            "time_ms": int(elapsed * 1000),
            "raw_m": measured,
            "corrected_m": measured,
            "valid": True,
        }
        yield json.dumps(message)
        await asyncio.sleep(0.2)


async def tcp_source() -> AsyncIterator[str]:
    host = os.getenv("UWB_TCP_HOST", "192.168.1.10")
    port = int(os.getenv("UWB_TCP_PORT", "3333"))
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            runtime.connected = True
            runtime.last_error = None
            while line := await reader.readline():
                yield line.decode("utf-8", errors="replace")
            writer.close()
            await writer.wait_closed()
        except (OSError, ValueError) as error:
            runtime.last_error = str(error)
        finally:
            runtime.connected = False
        await asyncio.sleep(2)


async def serial_source() -> AsyncIterator[str]:
    import serial

    port = os.getenv("UWB_SERIAL_PORT", "/dev/tty.usbmodem1")
    baud = int(os.getenv("UWB_SERIAL_BAUD", "115200"))
    while True:
        try:
            with serial.Serial(port, baudrate=baud, timeout=1) as connection:
                runtime.connected = True
                runtime.last_error = None
                while True:
                    line = await asyncio.to_thread(connection.readline)
                    if line:
                        yield line.decode("utf-8", errors="replace")
        except (OSError, serial.SerialException) as error:
            runtime.last_error = str(error)
        finally:
            runtime.connected = False
        await asyncio.sleep(2)


async def replay_source() -> AsyncIterator[str]:
    path = Path(os.environ["UWB_REPLAY_FILE"])
    runtime.connected = True
    try:
        while True:
            with path.open(encoding="utf-8") as replay:
                for line in replay:
                    message = json.loads(line)
                    compact = {
                        key: message[key]
                        for key in (
                            "type",
                            "v",
                            "board",
                            "peer",
                            "seq",
                            "time_ms",
                            "raw_m",
                            "corrected_m",
                            "valid",
                        )
                    }
                    yield json.dumps(compact)
                    await asyncio.sleep(float(os.getenv("UWB_REPLAY_INTERVAL", "0.2")))
    finally:
        runtime.connected = False


async def acquisition_loop() -> None:
    sources = {"demo": demo_source, "tcp": tcp_source, "serial": serial_source, "replay": replay_source}
    factory = sources.get(runtime.source)
    if factory is None:
        runtime.last_error = f"unknown source: {runtime.source}"
        return
    if runtime.source == "demo":
        runtime.connected = True
    try:
        async for line in factory():
            await runtime.ingest(line)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        runtime.connected = False
        runtime.last_error = str(error)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    runtime.task = asyncio.create_task(acquisition_loop())
    yield
    if runtime.task is not None:
        runtime.task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.task
    if runtime.recorder.active:
        runtime.recorder.stop()


app = FastAPI(title="UWB Lab", lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PACKAGE_DIR / "static" / "index.html")


@app.get("/app.css")
async def stylesheet() -> FileResponse:
    return FileResponse(PACKAGE_DIR / "static" / "app.css", media_type="text/css")


@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(PACKAGE_DIR / "static" / "app.js", media_type="text/javascript")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return runtime.status()


@app.get("/api/samples")
async def samples() -> dict[str, Any]:
    return {"samples": list(runtime.state.samples)}


@app.post("/api/prior")
async def set_prior(payload: PriorInput) -> dict[str, Any]:
    try:
        prior = runtime.state.set_prior(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    runtime.recorder.record_prior(prior)
    return prior.to_dict()


@app.post("/api/recording/start")
async def start_recording(payload: RecordingInput) -> dict[str, Any]:
    try:
        path = runtime.recorder.start(payload.name, runtime.source)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if runtime.state.prior:
        runtime.recorder.record_prior(runtime.state.prior)
    return {"recording": True, "session": path.name}


@app.post("/api/recording/stop")
async def stop_recording() -> dict[str, Any]:
    try:
        path = runtime.recorder.stop()
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"recording": False, "session": path.name}


@app.websocket("/ws")
async def websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime.clients.add(websocket)
    await websocket.send_json({"type": "snapshot", "samples": list(runtime.state.samples)})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.clients.discard(websocket)
