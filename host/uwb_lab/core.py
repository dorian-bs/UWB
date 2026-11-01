from __future__ import annotations

import json
import math
import os
import re
import statistics
import uuid
from collections import deque
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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


def _bounded_int(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if not lower <= value <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    return value


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


V2_DIAGNOSTIC_FIELDS: dict[str, tuple[int, int]] = {
    "ipatov_power": (0, 0xFFFFFFFF),
    "ipatov_f1": (0, 0xFFFFFFFF),
    "ipatov_f2": (0, 0xFFFFFFFF),
    "ipatov_f3": (0, 0xFFFFFFFF),
    "ipatov_fp_index": (0, 0xFFFF),
    "ipatov_accum_count": (0, 0xFFFF),
    "cia_diag1": (0, 0xFFFFFFFF),
}


# DW3000 User Manual 4.7.2/4.7.3 constant for a 64 MHz PRF. Assumed, not read from the radio.
RX_LEVEL_OFFSET_DB = 121.7


def derive_diagnostics(sample: dict[str, Any]) -> dict[str, float | None]:
    """Derive power metrics (dB) from raw v2 diagnostics. Raw fields are left untouched.

    fp_gap_db = RX power - first-path power. A large gap means the first path is weak
    compared to the total energy, which is the usual multipath / NLOS indicator.
    """
    count = sample.get("ipatov_accum_count")
    if not count:
        return {"rx_power_dbm": None, "fp_power_dbm": None, "fp_gap_db": None}
    rx_power = None
    fp_power = None
    if sample["ipatov_power"] > 0:
        rx_power = 10 * math.log10(sample["ipatov_power"] * 2**21 / count**2) - RX_LEVEL_OFFSET_DB
    fp_energy = sample["ipatov_f1"] ** 2 + sample["ipatov_f2"] ** 2 + sample["ipatov_f3"] ** 2
    if fp_energy > 0:
        fp_power = 10 * math.log10(fp_energy / count**2) - RX_LEVEL_OFFSET_DB
    gap = rx_power - fp_power if rx_power is not None and fp_power is not None else None
    return {"rx_power_dbm": rx_power, "fp_power_dbm": fp_power, "fp_gap_db": gap}


def parse_range_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{") or len(line) > 2048:
        return None
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(message, dict) or message.get("type") != "range":
        return None
    version = message.get("v")
    if version not in (1, 2):
        return None

    try:
        sample = {
            "type": "range",
            "v": int(version),
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
    if sample["v"] == 2:
        try:
            for field, (lower, upper) in V2_DIAGNOSTIC_FIELDS.items():
                sample[field] = _bounded_int(message[field], field, lower, upper)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    if "radio_revision" in message:
        revision = message["radio_revision"]
        if type(revision) is not int or not 0 <= revision <= 0xFFFFFFFF:
            return None
        sample["radio_revision"] = revision
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

        if sample["v"] == 2:
            sample.update(derive_diagnostics(sample))
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
        self.radio_file: Any = None
        self.started_at: str | None = None
        self.sample_count = 0

    @property
    def active(self) -> bool:
        return self.session_dir is not None

    def start(self, name: str, source: str, *, parent: Path | None = None) -> Path:
        if self.active:
            raise ValueError("a recording is already active")
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-") or "session"
        stamp = datetime.now(UTC).strftime("%y%m%d-%H%M")
        base = (parent if parent is not None else self.root) / f"{stamp}-{safe_name}"
        session_dir, attempt = base, 1
        while session_dir.exists():
            attempt += 1
            session_dir = base.with_name(f"{base.name}-{attempt}")
        session_dir.mkdir(parents=True, exist_ok=False)
        self.started_at = utc_now()
        manifest = {
            "format_version": 1,
            "name": name.strip() or "session",
            "source": source,
            "started_at": self.started_at,
            "completed": False,
        }
        write_json_atomic(session_dir / "manifest.json", manifest)
        with ExitStack() as stack:
            telemetry = stack.enter_context((session_dir / "telemetry.ndjson").open("a", encoding="utf-8"))
            priors = stack.enter_context((session_dir / "priors.ndjson").open("a", encoding="utf-8"))
            radio = stack.enter_context((session_dir / "radio.ndjson").open("a", encoding="utf-8"))
            self.telemetry_file, self.priors_file, self.radio_file = telemetry, priors, radio
            stack.pop_all()
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

    def record_radio(self, event: dict[str, Any]) -> None:
        if self.radio_file is not None:
            self.radio_file.write(json.dumps(event, separators=(",", ":")) + "\n")
            self.radio_file.flush()

    def stop(self, *, completed: bool = True, reason: str | None = None) -> Path:
        if self.session_dir is None:
            raise ValueError("no recording is active")
        session_dir = self.session_dir
        errors: list[OSError] = []
        try:
            for stream in (self.telemetry_file, self.priors_file, self.radio_file):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError as error:
                        errors.append(error)
            manifest_path = session_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update(
                {
                    "completed": completed and not errors,
                    "ended_at": utc_now(),
                    "sample_count": self.sample_count,
                    "reason": "; ".join(str(error) for error in errors) if errors else reason,
                }
            )
            write_json_atomic(manifest_path, manifest)
            if errors:
                raise OSError(f"Recording close failed: {manifest['reason']}") from errors[0]
        finally:
            self.session_dir = None
            self.telemetry_file = None
            self.priors_file = None
            self.radio_file = None
            self.sample_count = 0
        return session_dir
