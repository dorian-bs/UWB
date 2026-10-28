from __future__ import annotations

import asyncio
import math
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .core import utc_now, write_json_atomic
from .radio import DELAY_FIELDS, ControlError, RadioControl

# Equal increments to all four TX/RX registers reduce pair ToF by two increments.
# Match DWT_TIME_UNITS and QORVO_SPEED_OF_LIGHT_M_S in the firmware.
METRES_PER_DTU = 2 * 299702547.0 / (499.2e6 * 128)


class CalibrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_distance_m: float = Field(gt=0, le=1000, allow_inf_nan=False)
    target_samples: int = Field(default=100, ge=2, le=10000, strict=True)
    tolerance_m: float = Field(default=0.01, ge=0, le=1000, allow_inf_nan=False)


class CalibrationApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)


class CalibrationCapture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.result: dict[str, Any] = {"phase": "idle", "error": None}
        self.samples: list[dict[str, Any]] = []
        self.baseline: dict[str, dict[str, Any]] = {}
        self.connections: dict[str, str | None] = {}
        self.started = 0.0
        self.last_received = 0.0
        self.last_identity: tuple[int, int] | None = None
        self.inputs = CalibrationInput(known_distance_m=1)

    @property
    def active(self) -> bool:
        if self.result["phase"] == "capturing" and time.monotonic() - self.last_received > 30:
            self.invalidate("No fresh measurements for 30 seconds; capture again")
        return self.result["phase"] in ("capturing", "applying")

    def status(self) -> dict[str, Any]:
        active = self.active
        stats = {}
        if self.samples:
            for field, name in (("corrected_m", "corrected"), ("raw_m", "raw")):
                values = [sample[field] for sample in self.samples]
                stats[name] = {
                    "mean": statistics.fmean(values),
                    "std": statistics.pstdev(values),
                    "min": min(values),
                    "max": max(values),
                }
            stats["error_m"] = stats["corrected"]["mean"] - self.inputs.known_distance_m
        return {**self.result, **stats, "active": active, "count": len(self.samples)}

    def invalidate(self, reason: str) -> None:
        if self.result["phase"] in ("capturing", "ready"):
            self.result.update(phase="invalid", error=reason)

    def check_boards(self, control: RadioControl) -> None:
        for role, board in control.boards.items():
            status = board.status()
            if (
                not status["connected"]
                or status.get("connection_id") != self.connections.get(role)
                or status["state"] != self.baseline.get(role)
            ):
                self.invalidate("Board connection or settings changed; capture again")

    async def start(self, control: RadioControl, inputs: CalibrationInput) -> dict[str, Any]:
        if self.active:
            raise ControlError("Calibration already in progress")
        await control.refresh()
        status = control.status()
        if not status["ready"]:
            raise ControlError("Both USB boards must be ready and running")
        baseline = {role: dict(board["state"]) for role, board in status["boards"].items()}
        if any(state.get(key) is None for state in baseline.values() for key in DELAY_FIELDS):
            raise ControlError("Calibration requires updated firmware on both boards")
        self.baseline = baseline
        self.connections = {role: board.get("connection_id") for role, board in status["boards"].items()}
        self.inputs = inputs
        self.samples = []
        self.last_identity = None
        self.started = self.last_received = time.monotonic()
        self.result = {
            "id": uuid.uuid4().hex,
            "phase": "capturing",
            "error": None,
            "skipped": 0,
            "created_at": utc_now(),
            "parameters": inputs.model_dump(),
            "before": baseline,
            "method": "equal-four-registers-v1",
            "metres_per_dtu": METRES_PER_DTU,
        }
        return self.status()

    def observe(
        self, sample: dict[str, Any], control: RadioControl, received_at: float | None = None
    ) -> None:
        if self.result["phase"] == "capturing" and not self.active:
            return
        if self.result["phase"] not in ("capturing", "ready"):
            return
        self.check_boards(control)
        if self.result["phase"] == "invalid":
            return
        if received_at is not None and received_at < self.started:
            return
        if sample["board"] != self.baseline["tx"]["board"] or sample["peer"] != self.baseline["rx"]["board"]:
            self.invalidate("Measurement identity changed; capture again")
            return
        if sample.get("radio_revision") != self.baseline["tx"]["revision"]:
            self.invalidate("Measurement radio revision changed; capture again")
            return
        identity = (sample["seq"], sample["time_ms"])
        if self.last_identity and any(
            new <= old for new, old in zip(identity, self.last_identity, strict=True)
        ):
            self.invalidate("Measurement sequence or clock reset; capture again")
            return
        self.last_identity = identity
        self.last_received = time.monotonic()
        if self.result["phase"] != "capturing":
            return
        if sample["valid"] is not True:
            self.result["skipped"] += 1
            return
        if abs(sample["raw_m"] - sample["corrected_m"]) > 0.00001:
            self.invalidate(
                "Existing range correction detected; cannot calibrate antenna delays from corrected data"
            )
            return
        self.samples.append(dict(sample))
        if len(self.samples) == self.inputs.target_samples:
            self._evaluate()

    def _evaluate(self) -> None:
        corrected = [s["corrected_m"] for s in self.samples]
        raw = [s["raw_m"] for s in self.samples]

        def summary(values: list[float]) -> dict[str, float]:
            return {
                "mean": statistics.fmean(values),
                "std": statistics.pstdev(values),
                "min": min(values),
                "max": max(values),
            }

        error = statistics.fmean(corrected) - self.inputs.known_distance_m
        magnitude = math.floor(abs(error) / METRES_PER_DTU + 0.5)
        delta = (magnitude if error > 0 else -magnitude) if abs(error) > self.inputs.tolerance_m else 0
        proposed = {
            role: {key: state[key] + delta for key in DELAY_FIELDS} for role, state in self.baseline.items()
        }
        self.result.update(
            phase="ready",
            corrected=summary(corrected),
            raw=summary(raw),
            error_m=error,
            delta_dtu=delta,
            proposed=proposed,
            evaluated_at=utc_now(),
        )
        if any(not 0 <= value <= 65535 for values in proposed.values() for value in values.values()):
            self.invalidate("Proposed antenna delay is outside the 0-65535 range")

    def stop(self) -> dict[str, Any]:
        self.invalidate("Capture stopped or reset; capture again before applying")
        return self.status()

    def _save(self) -> None:
        folder = self.root / "calibrations"
        folder.mkdir(parents=True, exist_ok=True)
        write_json_atomic(folder / f"{self.result['id']}.json", {**self.status(), "samples": self.samples})

    async def apply(self, control: RadioControl, identifier: str) -> dict[str, Any]:
        self.check_boards(control)
        if self.result.get("id") != identifier or self.result["phase"] != "ready":
            raise ControlError(
                self.result.get("error") or "A completed fresh calibration capture is required"
            )
        if time.monotonic() - self.last_received > 30:
            self.invalidate("Measurements are stale; capture again")
            raise ControlError(self.result["error"])
        if self.result["delta_dtu"] == 0:
            raise ControlError("No hardware change needed: within tolerance or below one DTU")
        self.result["phase"] = "applying"
        try:
            # Persist intent before any command: a host crash must not look like a confirmed apply.
            self._save()
            after = await control.apply_calibration(self.result["delta_dtu"], self.baseline)
        except (ControlError, OSError, asyncio.CancelledError) as error:
            self.result.update(
                phase="failed", error=str(error) or "Calibration apply cancelled", after=control.status()
            )
            self._save()
            raise
        self.result.update(phase="applied", after=after, applied_at=utc_now())
        self._save()
        return self.status()
