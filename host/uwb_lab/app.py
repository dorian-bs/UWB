from __future__ import annotations

import asyncio
import json
import logging
import math
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

from .analysis import METHODS, AnalysisRequest
from .analysis_jobs import AnalysisService
from .calibration import CalibrationApplyInput, CalibrationCapture, CalibrationInput
from .core import RX_LEVEL_OFFSET_DB, LabState, SessionRecorder
from .experiments import BatchInput, BatchRunner
from .radio import ControlError, RadioConfig, RadioControl, SampleRate, SerialBoard

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("UWB_DATA_DIR", PACKAGE_DIR.parent.parent / "data" / "sessions"))
logger = logging.getLogger(__name__)


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
    def __init__(self, data_dir: Path | None = None) -> None:
        self.source = os.getenv("UWB_SOURCE", "demo")
        self.state = LabState()
        self.recorder = SessionRecorder(data_dir if data_dir is not None else DATA_DIR)
        self.calibration = CalibrationCapture(self.recorder.root)
        self.experiments = BatchRunner(self.recorder.root, self.recorder, health_snapshot=self.health)
        self.analysis = AnalysisService(self.experiments)
        self.operation_lock = asyncio.Lock()
        self.experiment_task: asyncio.Task[None] | None = None
        self.clients: set[WebSocket] = set()
        self.connected = False
        self.last_error: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.sample_rate = SampleRate()
        self.radio: RadioControl | None = None
        if self.source == "dual-serial":
            tx_port = os.getenv("UWB_SERIAL_PORT", "")
            rx_port = os.getenv("UWB_RESPONDER_PORT", "")
            if not tx_port or not rx_port or Path(tx_port).resolve() == Path(rx_port).resolve():
                self.last_error = "dual-serial requires two distinct USB serial ports"
            else:
                self.radio = RadioControl(
                    SerialBoard(tx_port, "tx"), SerialBoard(rx_port, "rx"), self.recorder
                )

    def health(self) -> dict[str, int]:
        values = {
            key: getattr(self.state, key)
            for key in ("total_samples", "ignored_lines", "malformed_lines", "sequence_gaps")
        }
        if self.radio:
            values.update(
                {
                    f"{role}_telemetry_dropped": board.status().get("telemetry_dropped", 0)
                    for role, board in self.radio.boards.items()
                }
            )
        return values

    def set_connected(self, connected: bool, reason: str | None = None) -> None:
        self.connected = connected
        if not connected:
            self.calibration.invalidate(reason or "Acquisition disconnected")
            self.experiments.request_stop("interrupted", reason or "Acquisition disconnected")

    @asynccontextmanager
    async def controls(self, *, batch: bool = False, calibration: bool = False) -> AsyncIterator[None]:
        if (
            self.operation_lock.locked()
            or (self.experiments.active and not batch)
            or (self.calibration.active and not calibration)
        ):
            raise HTTPException(status_code=409, detail="Experiment or configuration owns these controls")
        async with self.operation_lock:
            yield

    async def ingest(self, line: str, received_at: float | None = None) -> None:
        sample = self.state.add_line(line)
        if sample is None:
            return
        if sample["valid"]:
            self.sample_rate.add(received_at)
        if self.radio is not None:
            self.calibration.observe(sample, self.radio, received_at)
        try:
            if self.experiments.active:
                self.experiments.record_sample(sample)
            else:
                self.recorder.record_sample(sample)
        except OSError as error:
            logger.exception("Recording failed")
            self.last_error = f"Recording failed: {error}"
            if self.experiments.active:
                try:
                    self.experiments.stop("failed", self.last_error)
                except OSError as close_error:
                    logger.exception("Failed to finalize recording: %s", close_error)
        stale_clients: list[WebSocket] = []
        for client in tuple(self.clients):
            try:
                await asyncio.wait_for(client.send_json({"type": "sample", "sample": sample}), timeout=0.5)
            except (TimeoutError, WebSocketDisconnect, RuntimeError, OSError) as error:
                logger.warning("Disconnecting dashboard client after telemetry delivery failed: %s", error)
                stale_clients.append(client)
        for client in stale_clients:
            self.clients.discard(client)
            try:
                await asyncio.wait_for(client.close(code=1013), timeout=0.5)
            except (TimeoutError, WebSocketDisconnect, RuntimeError, OSError) as error:
                logger.warning("Dashboard client could not be closed: %s", error)

    def status(self) -> dict[str, Any]:
        if self.radio:
            self.calibration.check_boards(self.radio)
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
            "observed_hz": self.sample_rate.value(),
            "radio": self.radio.status() if self.radio else {"supported": False},
            "experiment": self.experiments.status(),
            "calibration": self.calibration.status(),
        }


runtime = Runtime()


async def demo_source() -> AsyncIterator[str]:
    sequence = 0
    started = time.monotonic()
    while True:
        sequence += 1
        elapsed = time.monotonic() - started
        true_distance = 2.0 + 0.25 * math.sin(elapsed / 8)
        multipath = random.random() < 0.15
        measured = true_distance + random.gauss(0, 0.025) + (abs(random.gauss(0, 0.12)) if multipath else 0.0)
        count = 128
        rx_dbm = random.gauss(-80, 0.7)
        fp_dbm = rx_dbm - (random.uniform(7, 14) if multipath else random.uniform(1, 4))
        fp_amplitude = math.sqrt(count**2 * 10 ** ((fp_dbm + RX_LEVEL_OFFSET_DB) / 10) / 3)
        message = {
            "type": "range",
            "v": 2,
            "board": 1,
            "peer": 2,
            "seq": sequence,
            "time_ms": int(elapsed * 1000),
            "raw_m": measured,
            "corrected_m": measured,
            "valid": True,
            "ipatov_power": int(count**2 / 2**21 * 10 ** ((rx_dbm + RX_LEVEL_OFFSET_DB) / 10)),
            "ipatov_f1": int(fp_amplitude),
            "ipatov_f2": int(fp_amplitude),
            "ipatov_f3": int(fp_amplitude),
            "ipatov_fp_index": 0,
            "ipatov_accum_count": count,
            "cia_diag1": 0,
        }
        yield json.dumps(message)
        await asyncio.sleep(0.2)


async def tcp_source() -> AsyncIterator[str]:
    host = os.getenv("UWB_TCP_HOST")
    if not host:
        raise ValueError("TCP source requires --tcp-host or UWB_TCP_HOST")
    port = int(os.getenv("UWB_TCP_PORT", "3333"))
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            runtime.set_connected(True)
            runtime.last_error = None
            while line := await reader.readline():
                yield line.decode("utf-8", errors="replace")
            writer.close()
            await writer.wait_closed()
        except (OSError, ValueError) as error:
            runtime.last_error = str(error)
        finally:
            runtime.set_connected(False, runtime.last_error)
        await asyncio.sleep(2)


async def serial_source() -> AsyncIterator[str]:
    import serial

    port = os.getenv("UWB_SERIAL_PORT")
    if not port:
        raise ValueError("Serial source requires --serial-port or UWB_SERIAL_PORT")
    baud = int(os.getenv("UWB_SERIAL_BAUD", "115200"))
    while True:
        try:
            with serial.Serial(port, baudrate=baud, timeout=1) as connection:
                runtime.set_connected(True)
                runtime.last_error = None
                while True:
                    line = await asyncio.to_thread(connection.readline)
                    if line:
                        yield line.decode("utf-8", errors="replace")
        except (OSError, serial.SerialException) as error:
            runtime.last_error = str(error)
        finally:
            runtime.set_connected(False, runtime.last_error)
        await asyncio.sleep(2)


async def replay_source() -> AsyncIterator[str]:
    path = Path(os.environ["UWB_REPLAY_FILE"])
    runtime.set_connected(True)
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
                    if "radio_revision" in message:
                        compact["radio_revision"] = message["radio_revision"]
                    for optional_key in (
                        "ipatov_power",
                        "ipatov_f1",
                        "ipatov_f2",
                        "ipatov_f3",
                        "ipatov_fp_index",
                        "ipatov_accum_count",
                        "cia_diag1",
                    ):
                        if optional_key in message:
                            compact[optional_key] = message[optional_key]
                    yield json.dumps(compact)
                    await asyncio.sleep(float(os.getenv("UWB_REPLAY_INTERVAL", "0.2")))
    finally:
        runtime.set_connected(False)


async def acquisition_loop() -> None:
    if runtime.source == "dual-serial":
        try:
            await dual_serial_acquisition()
        except (ControlError, OSError) as error:
            runtime.last_error = str(error)
            runtime.set_connected(False, str(error))
        return
    sources = {"demo": demo_source, "tcp": tcp_source, "serial": serial_source, "replay": replay_source}
    factory = sources.get(runtime.source)
    if factory is None:
        runtime.last_error = f"unknown source: {runtime.source}"
        return
    if runtime.source == "demo":
        runtime.set_connected(True)
    try:
        async for line in factory():
            await runtime.ingest(line)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        runtime.set_connected(False, str(error))
        runtime.last_error = str(error)


async def dual_serial_acquisition() -> None:
    control = runtime.radio
    if control is None:
        return
    tasks = [
        asyncio.create_task(
            board.run(runtime.ingest, on_disconnect=lambda reason: runtime.set_connected(False, reason))
        )
        for board in control.boards.values()
    ]
    try:
        while True:
            for task in tasks:
                if task.done():
                    await task
                    raise ControlError("USB acquisition stopped unexpectedly")
            runtime.set_connected(all(board.connected for board in control.boards.values()))
            if runtime.connected and not control.busy:
                try:
                    await control.refresh()
                except ControlError as error:
                    runtime.last_error = str(error)
                else:
                    runtime.last_error = None
            else:
                errors = [
                    board.status()["error"] for board in control.boards.values() if board.status()["error"]
                ]
                runtime.last_error = "; ".join(errors) or "Waiting for both USB boards"
            await asyncio.sleep(2)
    finally:
        runtime.set_connected(False)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if runtime.analysis.closing:
        runtime.analysis = AnalysisService(runtime.experiments)
    runtime.task = asyncio.create_task(acquisition_loop())
    try:
        yield
    finally:
        try:
            if runtime.experiments.active:
                runtime.experiments.stop("interrupted", "Host shutdown")
        except OSError as error:
            runtime.last_error = f"Shutdown recording failure: {error}"
            logger.exception("Cannot finalize experiment during shutdown")
        if runtime.experiment_task is not None:
            await runtime.experiment_task
        try:
            await runtime.analysis.close()
        finally:
            if runtime.task is not None:
                runtime.task.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime.task
            if runtime.recorder.active:
                runtime.recorder.stop()


app = FastAPI(title="UWB Lab", lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PACKAGE_DIR / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/app.css")
async def stylesheet() -> FileResponse:
    return FileResponse(
        PACKAGE_DIR / "static" / "app.css", media_type="text/css", headers={"Cache-Control": "no-store"}
    )


@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(
        PACKAGE_DIR / "static" / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"}
    )


@app.get("/experiments.js", response_class=FileResponse)
async def experiment_javascript() -> FileResponse:
    return FileResponse(
        PACKAGE_DIR / "static" / "experiments.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/analysis.js")
async def analysis_javascript() -> FileResponse:
    return FileResponse(
        PACKAGE_DIR / "static" / "analysis.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return runtime.status()


@app.get("/api/samples")
async def samples() -> dict[str, Any]:
    return {"samples": list(runtime.state.samples)}


@app.post("/api/radio")
async def set_radio(payload: RadioConfig) -> dict[str, Any]:
    if runtime.radio is None:
        raise HTTPException(status_code=409, detail="Radio control requires the dual-serial hardware source")
    try:
        async with runtime.controls():
            return await runtime.radio.apply(payload)
    except ControlError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        runtime.radio.last_error = f"Radio recording failed: {error}"
        raise HTTPException(status_code=500, detail=runtime.radio.last_error) from error


@app.post("/api/prior")
async def set_prior(payload: PriorInput) -> dict[str, Any]:
    async with runtime.controls():
        try:
            prior = runtime.state.set_prior(payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        runtime.recorder.record_prior(prior)
    return prior.to_dict()


def calibration_radio() -> RadioControl:
    if runtime.source != "dual-serial" or runtime.radio is None or not runtime.connected:
        raise HTTPException(status_code=409, detail="Calibration apply requires both USB-connected boards")
    return runtime.radio


@app.post("/api/calibration/start")
async def start_calibration(payload: CalibrationInput) -> dict[str, Any]:
    async with runtime.controls():
        try:
            return await runtime.calibration.start(calibration_radio(), payload)
        except ControlError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/calibration/stop")
async def stop_calibration() -> dict[str, Any]:
    async with runtime.controls(calibration=True):
        return runtime.calibration.stop()


@app.post("/api/calibration/apply")
async def apply_calibration(payload: CalibrationApplyInput) -> dict[str, Any]:
    async with runtime.controls():
        try:
            return await runtime.calibration.apply(calibration_radio(), payload.id)
        except ControlError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except OSError as error:
            logger.exception("Calibration audit storage failed")
            runtime.last_error = (
                f"Calibration audit storage failed; inspect board state before retrying: {error}"
            )
            raise HTTPException(status_code=500, detail=runtime.last_error) from error


@app.post("/api/recording/start")
async def start_recording(payload: RecordingInput) -> dict[str, Any]:
    async with runtime.controls():
        try:
            path = runtime.recorder.start(payload.name, runtime.source)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if runtime.state.prior:
            runtime.recorder.record_prior(runtime.state.prior)
        if runtime.radio:
            runtime.radio.record("snapshot")
    return {"recording": True, "session": path.name}


@app.post("/api/recording/stop")
async def stop_recording() -> dict[str, Any]:
    async with runtime.controls():
        try:
            path = runtime.recorder.stop()
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return {"recording": False, "session": path.name}


@app.post("/api/experiments/start")
async def start_experiment(payload: BatchInput) -> dict[str, Any]:
    async with runtime.controls(batch=True):
        if runtime.experiments.active or runtime.recorder.active:
            raise HTTPException(status_code=409, detail="Another batch or recording is active")
        if not runtime.connected or runtime.state.prior is None:
            raise HTTPException(
                status_code=409, detail="A connected source and configured prior are required"
            )
        if runtime.radio and runtime.radio.busy:
            raise HTTPException(status_code=409, detail="Radio configuration is in progress")
        if runtime.experiment_task is not None and not runtime.experiment_task.done():
            runtime.experiment_task.cancel()
            with suppress(asyncio.CancelledError):
                await runtime.experiment_task
        try:
            result = runtime.experiments.start(
                payload,
                source=runtime.source,
                prior=runtime.state.prior,
                radio_snapshot=runtime.radio.status() if runtime.radio else None,
            )
        except OSError as error:
            logger.exception("Cannot start experiment storage")
            raise HTTPException(status_code=500, detail=str(error)) from error
        runtime.experiment_task = asyncio.create_task(runtime.experiments.supervise())
        return result


@app.post("/api/experiments/next")
async def start_next_run() -> dict[str, Any]:
    async with runtime.controls(batch=True):
        try:
            return runtime.experiments.begin_waiting_run()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except OSError as error:
            logger.exception("Cannot start run")
            raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/api/experiments/stop")
async def stop_experiment() -> dict[str, Any]:
    async with runtime.controls(batch=True):
        try:
            runtime.experiments.stop("stopped", "Stopped by operator")
        except OSError as error:
            logger.exception("Cannot finalize experiment")
            raise HTTPException(status_code=500, detail=str(error)) from error
        return runtime.experiments.status()


@app.get("/api/experiments")
async def experiments() -> dict[str, Any]:
    try:
        return {"batches": runtime.experiments.list_batches()}
    except (OSError, ValueError, KeyError) as error:
        logger.exception("Cannot read experiment archives")
        raise HTTPException(status_code=500, detail=f"Cannot read experiment archives: {error}") from error


@app.get("/api/experiments/{batch_id}")
async def experiment(batch_id: str) -> dict[str, Any]:
    try:
        return runtime.experiments.load_batch(batch_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Experiment not found") from error
    except (OSError, ValueError, KeyError) as error:
        logger.exception("Cannot read experiment")
        raise HTTPException(status_code=500, detail=f"Cannot read experiment: {error}") from error


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


@app.get("/api/analysis/methods")
async def analysis_methods() -> dict[str, Any]:
    return {"methods": METHODS, "parameters": AnalysisRequest.model_json_schema()}


@app.post("/api/analysis/jobs", status_code=202)
async def submit_analysis(payload: AnalysisRequest) -> dict[str, Any]:
    try:
        return runtime.analysis.submit(payload)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Experiment not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (OSError, KeyError) as error:
        logger.exception("Cannot prepare analysis")
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/analysis/jobs/{job_id}")
async def analysis_job(job_id: str) -> dict[str, Any]:
    try:
        return runtime.analysis.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Analysis job not found or expired") from error


@app.get("/api/experiments/{batch_id}/analyses")
async def saved_analyses(batch_id: str) -> dict[str, Any]:
    try:
        runtime.experiments.load_batch(batch_id)
        directory = runtime.experiments.batch_path(batch_id) / "analyses"
        return {"analyses": [{"result_id": path.stem} for path in sorted(directory.glob("*.json"))]}
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Experiment not found") from error
    except (OSError, ValueError, KeyError) as error:
        logger.exception("Cannot read saved analyses")
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/api/experiments/{batch_id}/analyses/{result_id}")
async def saved_analysis(batch_id: str, result_id: str) -> dict[str, Any]:
    if len(result_id) != 32 or any(character not in "0123456789abcdef" for character in result_id):
        raise HTTPException(status_code=400, detail="Invalid result identity")
    try:
        base = runtime.experiments.batch_path(batch_id).resolve()
        path = (base / "analyses" / f"{result_id}.json").resolve()
        if not path.is_relative_to(base):
            raise ValueError("Result path escapes the experiment")
        return await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Saved analysis not found") from error
    except (OSError, ValueError) as error:
        logger.exception("Cannot read saved analysis")
        raise HTTPException(status_code=500, detail=str(error)) from error
