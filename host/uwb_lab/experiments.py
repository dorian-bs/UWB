from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ConfigDict, Field

from .analysis import Moments
from .core import Prior, SessionRecorder, utc_now, write_json_atomic

logger = logging.getLogger(__name__)


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(default="experiment", max_length=80)
    notes: str = Field(default="", max_length=200)
    duration_s: float = Field(default=300, gt=0)
    run_count: int = Field(default=3, ge=1, strict=True)
    interval_s: float = Field(default=10, ge=0)
    start_delay_s: float = Field(default=0, ge=0)
    manual_start: bool = False


class BatchRunner:
    def __init__(
        self,
        root: Path,
        recorder: SessionRecorder,
        *,
        clock: Callable[[], float] = time.monotonic,
        health_snapshot: Callable[[], dict[str, int]] = dict,
    ) -> None:
        self.root = root / "experiments"
        self.recorder = recorder
        self.clock = clock
        self.health_snapshot = health_snapshot
        self.active = False
        self.meta: dict[str, Any] | None = None
        self.directory: Path | None = None
        self.intervals: TextIO | None = None
        self.phase = "idle"
        self.index = 0
        self.deadline = 0.0
        self.phase_started = 0.0
        self.pending_stop: tuple[str, str] | None = None
        self.moments = {field: Moments() for field in ("raw_m", "corrected_m")}
        self.prior: Prior | None = None

    def _persist(self) -> None:
        if self.directory is not None:
            write_json_atomic(self.directory / "manifest.json", self.meta)

    def start(
        self,
        payload: BatchInput,
        *,
        source: str,
        prior: Prior,
        radio_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self.active or self.recorder.active:
            raise ValueError("Another batch or recording is active")
        self.directory = self.root / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.prior = prior
        self.meta = {
            "format_version": 1,
            "batch_id": self.directory.name,
            "config": payload.model_dump(),
            "source": source,
            "prior": prior.to_dict(),
            "radio": radio_snapshot,
            "started_at": utc_now(),
            "outcome": "running",
            "error": None,
            "runs": [
                {"run_id": f"run-{index + 1:04d}", "state": "planned", "session": None}
                for index in range(payload.run_count)
            ],
        }
        self.index = 0
        self.pending_stop = None
        self.active = True
        try:
            self.intervals = (self.directory / "intervals.ndjson").open("a", encoding="utf-8")
            self._persist()
            self._begin_run()
        except OSError as error:
            self.stop("failed", f"Recording startup failed: {error}")
            raise
        return self.status()

    def _begin_run(self) -> None:
        """Enter the waiting phase (manual mode), the start-delay countdown, or record immediately."""
        assert self.meta is not None
        config = self.meta["config"]
        self.phase_started = self.clock()
        if config["manual_start"]:
            self.phase = "waiting"
            self.deadline = self.phase_started
            self._persist()
        elif config["start_delay_s"] > 0:
            self.phase = "delay"
            self.deadline = self.phase_started + config["start_delay_s"]
            self._persist()
        else:
            self._open_run()

    def begin_waiting_run(self) -> dict[str, Any]:
        if not self.active or self.phase != "waiting":
            raise ValueError("No run is waiting to be started")
        assert self.meta is not None
        if self.meta["config"]["start_delay_s"] > 0:
            self.phase_started = self.clock()
            self.phase = "delay"
            self.deadline = self.phase_started + self.meta["config"]["start_delay_s"]
            self._persist()
        else:
            self._open_run()
        return self.status()

    def _open_run(self) -> None:
        assert self.meta is not None and self.directory is not None and self.prior is not None
        run = self.meta["runs"][self.index]
        path = self.recorder.start(run["run_id"], self.meta["source"], parent=self.directory / "runs")
        run.update(
            state="recording",
            session=str(path.relative_to(self.directory)),
            started_at=utc_now(),
            sample_count=0,
            excluded_count=0,
            health_start=self.health_snapshot(),
        )
        self.moments = {field: Moments() for field in ("raw_m", "corrected_m")}
        self.phase = "recording"
        self.phase_started = self.clock()
        self.recorder.record_prior(self.prior)
        if self.meta["radio"] is not None:
            self.recorder.record_radio(
                {
                    "event": "snapshot",
                    "host_time": utc_now(),
                    "boards": self.meta["radio"]["boards"],
                    "error": self.meta["radio"].get("last_error"),
                }
            )
        self.phase_started = self.clock()
        self.deadline = self.phase_started + self.meta["config"]["duration_s"]
        self._persist()

    def request_stop(self, outcome: str, reason: str) -> None:
        if self.active and self.pending_stop is None:
            self.pending_stop = (outcome, reason)

    def _close_run(self, state: str, reason: str | None = None) -> None:
        assert self.meta is not None
        run = self.meta["runs"][self.index]
        run.update(
            ended_at=utc_now(),
            duration_s=max(0, self.clock() - self.phase_started),
            summaries={field: moments.summary() for field, moments in self.moments.items()},
            summary=self.moments["corrected_m"].summary(),
        )
        end = self.health_snapshot()
        run["health_delta"] = {
            key: end[key] - value if end.get(key, value) >= value else None
            for key, value in run.get("health_start", {}).items()
            if key in end
        }
        try:
            if self.recorder.active:
                self.recorder.stop(completed=state == "completed", reason=reason)
        except OSError as error:
            run.update(state="failed", reason=f"Session finalization failed: {error}")
            raise
        run.update(state=state, reason=reason)

    def advance(self) -> None:
        if not self.active:
            return
        if self.pending_stop is not None:
            self.stop(*self.pending_stop)
            return
        if self.phase == "waiting" or self.clock() < self.deadline:
            return
        assert self.meta is not None
        if self.phase == "delay":
            self._open_run()
        elif self.phase == "recording":
            if not self.moments["corrected_m"].count:
                self.stop("failed", "No valid measurements in the run")
                return
            self._close_run("completed")
            if self.index + 1 == len(self.meta["runs"]):
                self.stop("completed", None)
                return
            self.phase_started = self.clock()
            if self.meta["config"]["manual_start"]:
                self.index += 1
                self.phase = "waiting"
                self.deadline = self.phase_started
            else:
                self.phase = "interval"
                self.deadline = self.phase_started + self.meta["config"]["interval_s"]
            self._persist()
            self.advance()
        elif self.phase == "interval":
            self.index += 1
            self.phase = "idle"
            self._begin_run()

    def record_sample(self, sample: dict[str, Any]) -> str | None:
        self.advance()
        if not self.active:
            return None
        assert self.meta is not None
        if self.phase != "recording":
            assert self.intervals is not None
            run_key = "after_run" if self.phase == "interval" else "before_run"
            self.intervals.write(
                json.dumps(
                    {"phase": self.phase, run_key: self.meta["runs"][self.index]["run_id"], "sample": sample},
                    allow_nan=False,
                )
                + "\n"
            )
            self.intervals.flush()
            return None
        self.recorder.record_sample(sample)
        run = self.meta["runs"][self.index]
        run["sample_count"] += 1
        expected = sample.get("expected_m")
        if sample["valid"] and isinstance(expected, (int, float)) and math.isfinite(expected):
            for field, moments in self.moments.items():
                moments.add(sample[field] - expected)
        else:
            run["excluded_count"] += 1
        run["summary"] = self.moments["corrected_m"].summary()
        run["summaries"] = {field: moments.summary() for field, moments in self.moments.items()}
        return run["run_id"]

    def stop(self, outcome: str, reason: str | None) -> None:
        if not self.active:
            return
        assert self.meta is not None
        errors = []
        try:
            if self.phase == "recording" and self.meta["runs"][self.index]["state"] == "recording":
                try:
                    self._close_run("failed" if outcome == "failed" else "incomplete", reason)
                except OSError as error:
                    errors.append(str(error))
            if self.intervals is not None:
                try:
                    self.intervals.close()
                except OSError as error:
                    errors.append(str(error))
            for run in self.meta["runs"]:
                if run["state"] == "planned":
                    run["state"] = "not_executed"
            self.meta.update(
                outcome="failed" if errors else outcome,
                error="; ".join(errors) if errors else reason,
                ended_at=utc_now(),
            )
            try:
                self._persist()
            except OSError as error:
                errors.append(str(error))
                self.meta.update(outcome="failed", error="; ".join(errors))
            if errors:
                raise OSError(f"Batch finalization failed: {'; '.join(errors)}")
        finally:
            self.active = False
            self.phase = "idle"
            self.intervals = None
            self.pending_stop = None

    async def supervise(self) -> None:
        while self.active:
            try:
                self.advance()
            except OSError as error:
                logger.exception("Experiment scheduling/storage failed")
                try:
                    self.stop("failed", str(error))
                except OSError:
                    logger.exception("Experiment finalization also failed; archive is incomplete")
            await asyncio.sleep(0.05)

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "batch_id": self.meta["batch_id"] if self.meta else None,
            "phase": self.phase,
            "outcome": self.meta["outcome"] if self.meta else None,
            "run_index": self.index + 1 if self.meta else 0,
            "run_id": self.meta["runs"][self.index]["run_id"] if self.meta else None,
            "run_count": len(self.meta["runs"]) if self.meta else 0,
            "elapsed_s": max(0, self.clock() - self.phase_started) if self.active else 0,
            "remaining_s": max(0, self.deadline - self.clock()) if self.active else 0,
            "prior": self.meta["prior"] if self.meta else None,
            "error": self.meta["error"] if self.meta else None,
        }

    def batch_path(self, batch_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", batch_id):
            raise ValueError("Invalid experiment identity")
        path = self.root / batch_id
        if path.is_symlink():
            raise ValueError("Experiment directory must not be a symbolic link")
        return path

    def load_batch(self, batch_id: str) -> dict[str, Any]:
        if self.meta and batch_id == self.meta["batch_id"]:
            return json.loads(json.dumps(self.meta))
        value = json.loads((self.batch_path(batch_id) / "manifest.json").read_text(encoding="utf-8"))
        if value["outcome"] == "running":
            value.update(outcome="interrupted", error="Host stopped before batch finalization")
            for run in value["runs"]:
                if run["state"] == "recording":
                    run["state"] = "incomplete"
                elif run["state"] == "planned":
                    run["state"] = "not_executed"
        return value

    def list_batches(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        return [
            self.load_batch(path.parent.name)
            for path in sorted(self.root.glob("*/manifest.json"), reverse=True)
        ]
