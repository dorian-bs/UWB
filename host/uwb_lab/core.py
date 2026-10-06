from __future__ import annotations

import json
import math
import re
import statistics
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Prior:
    revision: int
    mode: str
    expected_distance_m: float
    uncertainty_m: float
    node_a_m: tuple[float, float, float] | None
    node_b_m: tuple[float, float, float] | None
    note: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _point(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain x, y and z")
    return tuple(_finite_float(item, name) for item in value)  # type: ignore[return-value]


def make_prior(payload: dict[str, Any], revision: int) -> Prior:
    mode = str(payload.get("mode", "distance"))
    uncertainty = _finite_float(payload.get("uncertainty_m", 0.0), "uncertainty_m")
    if uncertainty < 0:
        raise ValueError("uncertainty_m cannot be negative")

    node_a = None
    node_b = None
    if mode == "distance":
        expected = _finite_float(payload.get("distance_m"), "distance_m")
    elif mode == "positions":
        node_a = _point(payload.get("node_a_m"), "node_a_m")
        node_b = _point(payload.get("node_b_m"), "node_b_m")
        expected = math.dist(node_a, node_b)
    else:
        raise ValueError("mode must be 'distance' or 'positions'")

    if expected < 0 or expected > 1000:
        raise ValueError("expected distance must be between 0 and 1000 metres")

    return Prior(
        revision=revision,
        mode=mode,
        expected_distance_m=expected,
        uncertainty_m=uncertainty,
        node_a_m=node_a,
        node_b_m=node_b,
        note=str(payload.get("note", ""))[:200],
        created_at=utc_now(),
    )


def parse_range_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{") or len(line) > 2048:
        return None
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(message, dict) or message.get("type") != "range" or message.get("v") != 1:
        return None

    try:
        sample = {
            "type": "range",
            "v": 1,
            "board": int(message["board"]),
            "peer": int(message["peer"]),
            "seq": int(message["seq"]),
            "time_ms": int(message["time_ms"]),
            "raw_m": _finite_float(message["raw_m"], "raw_m"),
            "corrected_m": _finite_float(message["corrected_m"], "corrected_m"),
            "valid": message["valid"] is True,
            "host_time": utc_now(),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if sample["seq"] < 0 or sample["time_ms"] < 0:
        return None
    if not 0 <= sample["raw_m"] <= 1000 or not 0 <= sample["corrected_m"] <= 1000:
        return None
    return sample


def is_json_like_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("{") and len(stripped) <= 2048


class LabState:
    def __init__(self, history_size: int = 300, rolling_size: int = 20) -> None:
        self.samples: deque[dict[str, Any]] = deque(maxlen=history_size)
        self.residuals: deque[float] = deque(maxlen=rolling_size)
        self.priors: list[Prior] = []
        self.total_samples = 0
        self.ignored_lines = 0
        self.malformed_lines = 0
        self.sequence_gaps = 0
        self.last_sequence: int | None = None

    @property
    def prior(self) -> Prior | None:
        return self.priors[-1] if self.priors else None

    def set_prior(self, payload: dict[str, Any]) -> Prior:
        prior = make_prior(payload, len(self.priors) + 1)
        self.priors.append(prior)
        self.residuals.clear()
        return prior

    def add_line(self, line: str) -> dict[str, Any] | None:
        sample = parse_range_line(line)
        if sample is None:
            if not line.strip():
                return None
            if is_json_like_line(line):
                self.malformed_lines += 1
            else:
                self.ignored_lines += 1
            return None

        sequence = sample["seq"]
        if self.last_sequence is not None and sequence > self.last_sequence + 1:
            self.sequence_gaps += sequence - self.last_sequence - 1
        self.last_sequence = sequence
        self.total_samples += 1

        prior = self.prior
        if prior is not None:
            residual = sample["corrected_m"] - prior.expected_distance_m
            self.residuals.append(residual)
            sample.update(
                {
                    "prior_revision": prior.revision,
                    "expected_m": prior.expected_distance_m,
                    "residual_m": residual,
                    "rolling_bias_m": statistics.fmean(self.residuals),
                    "rolling_std_m": statistics.pstdev(self.residuals),
                }
            )
        else:
            sample.update(
                {
                    "prior_revision": None,
                    "expected_m": None,
                    "residual_m": None,
                    "rolling_bias_m": None,
                    "rolling_std_m": None,
                }
            )
        self.samples.append(sample)
        return sample


class SessionRecorder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session_dir: Path | None = None
        self.telemetry_file: Any = None
        self.priors_file: Any = None
        self.started_at: str | None = None
        self.sample_count = 0

    @property
    def active(self) -> bool:
        return self.session_dir is not None

    def start(self, name: str, source: str) -> Path:
        if self.active:
            raise ValueError("a recording is already active")
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-") or "session"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        session_dir = self.root / f"{stamp}-{safe_name}"
        session_dir.mkdir(parents=True, exist_ok=False)
        self.started_at = utc_now()
        manifest = {
            "format_version": 1,
            "name": name.strip() or "session",
            "source": source,
            "started_at": self.started_at,
            "completed": False,
        }
        (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.telemetry_file = (session_dir / "telemetry.ndjson").open("a", encoding="utf-8")
        self.priors_file = (session_dir / "priors.ndjson").open("a", encoding="utf-8")
        self.sample_count = 0
        self.session_dir = session_dir
        return session_dir

    def record_sample(self, sample: dict[str, Any]) -> None:
        if self.telemetry_file is not None:
            self.telemetry_file.write(json.dumps(sample, separators=(",", ":")) + "\n")
            self.telemetry_file.flush()
            self.sample_count += 1

    def record_prior(self, prior: Prior) -> None:
        if self.priors_file is not None:
            self.priors_file.write(json.dumps(prior.to_dict(), separators=(",", ":")) + "\n")
            self.priors_file.flush()

    def stop(self) -> Path:
        if self.session_dir is None:
            raise ValueError("no recording is active")
        session_dir = self.session_dir
        if self.telemetry_file is not None:
            self.telemetry_file.close()
        if self.priors_file is not None:
            self.priors_file.close()
        manifest_path = session_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"completed": True, "ended_at": utc_now(), "sample_count": self.sample_count})
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.session_dir = None
        self.telemetry_file = None
        self.priors_file = None
        self.sample_count = 0
        return session_dir
