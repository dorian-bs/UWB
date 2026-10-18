from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from collections import OrderedDict, deque
from importlib.metadata import version
from typing import Any

import numpy as np

from .analysis import (
    METHODS,
    AnalysisRequest,
    AnalysisResult,
    AnalysisSample,
    Metric,
    describe,
    fit_gmm,
    histogram,
    pooled_power_spectrum,
)
from .core import utc_now, write_json_atomic
from .experiments import BatchRunner
from .radio import _settle

logger = logging.getLogger(__name__)
ANALYSIS_VERSION = "noise-3"


class AnalysisService:
    def __init__(self, runner: BatchRunner) -> None:
        self.runner = runner
        self.jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.pending: tuple[str, dict[str, Any]] | None = None
        self.worker: asyncio.Task[None] | None = None
        self.current_job: str | None = None
        self.closing = False
        self.batch_window: deque[tuple[str, dict[str, Any]]] = deque(maxlen=2000)
        self.run_window: deque[tuple[str, dict[str, Any]]] = deque(maxlen=2000)
        self.batch_id: str | None = None
        self.run_id: str | None = None
        self.last_live: tuple[str, str, float] | None = None

    def observe(self, batch_id: str, run_id: str, sample: dict[str, Any]) -> None:
        if self.batch_id != batch_id:
            self.batch_window.clear()
            self.run_window.clear()
            self.batch_id = batch_id
        if self.run_id != run_id:
            self.run_window.clear()
            self.run_id = run_id
        expected = sample.get("expected_m")
        if sample["valid"] and isinstance(expected, (int, float)) and math.isfinite(expected):
            value = (run_id, dict(sample))
            self.batch_window.append(value)
            self.run_window.append(value)

    def submit(self, request: AnalysisRequest) -> dict[str, Any]:
        if self.closing:
            raise ValueError("Analysis service is shutting down")
        batch = self.runner.load_batch(request.batch_id)
        runs = [run for run in batch["runs"] if run.get("session")]
        if request.scope == "run":
            runs = [run for run in runs if run["run_id"] == request.run_id]
        elif request.scope == "current":
            runs = runs[-1:]
        if not runs:
            raise ValueError("No recorded run matches this selection")
        if request.mode == "full" and any(run["state"] == "recording" for run in runs):
            raise ValueError("Full-data analysis requires closed runs")
        recipe = request.model_dump()
        key = hashlib.sha256(
            json.dumps(
                {
                    "recipe": recipe,
                    "runs": [(run["run_id"], run["state"]) for run in runs],
                    "batch_outcome": batch["outcome"],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if request.mode != "full" and self.last_live is not None:
            old_key, old_id, submitted = self.last_live
            if old_key == key and time.monotonic() - submitted < 2 and old_id in self.jobs:
                return self.jobs[old_id]
        buffered = (
            request.mode == "live"
            and self.runner.active
            and self.batch_id == request.batch_id
            and (request.scope == "batch" or runs[-1]["run_id"] == self.run_id)
        )
        records = None
        if buffered:
            source = self.batch_window if request.scope == "batch" else self.run_window
            selected = {run["run_id"] for run in runs}
            records = [(run_id, dict(sample)) for run_id, sample in source if run_id in selected]
            records = records[-request.window_size :]
        directory = self.runner.batch_path(request.batch_id)
        sources = []
        if records is None:
            for run in runs:
                path = (directory / run["session"] / "telemetry.ndjson").resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise ValueError("Recorded session path escapes the experiment")
                sources.append((run["run_id"], path, path.stat().st_size))
        job_id = uuid.uuid4().hex
        if self.pending:
            self.jobs[self.pending[0]]["state"] = "superseded"
        self.jobs[job_id] = {
            "job_id": job_id,
            "recipe_key": key,
            "recipe": recipe,
            "state": "queued",
            "created_at": utc_now(),
        }
        self.pending = (
            job_id,
            {
                "request": request,
                "batch": batch,
                "runs": runs,
                "records": records,
                "directory": directory,
                "sources": sources,
            },
        )
        while len(self.jobs) > 8:
            candidate = next(
                (
                    identifier
                    for identifier, job in self.jobs.items()
                    if job["state"] not in ("queued", "running")
                ),
                None,
            )
            if candidate is None:
                break
            del self.jobs[candidate]
        if request.mode != "full":
            self.last_live = key, job_id, time.monotonic()
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._drain())
            self.worker.add_done_callback(self._worker_done)
        return self.jobs[job_id]

    def _worker_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Unexpected analysis worker failure", exc_info=error)
            if self.current_job in self.jobs:
                self.jobs[self.current_job].update(state="failed", error=str(error))

    async def _drain(self) -> None:
        while self.pending is not None:
            identifier, snapshot = self.pending
            self.pending = None
            self.current_job = identifier
            job = self.jobs[identifier]
            job["state"] = "running"
            try:
                output = await asyncio.to_thread(self.compute, snapshot)
                if snapshot["request"].mode == "full":
                    destination = snapshot["directory"] / "analyses"
                    destination.mkdir(exist_ok=True)
                    await asyncio.to_thread(
                        write_json_atomic,
                        destination / f"{identifier}.json",
                        {"job_id": identifier, "recipe": job["recipe"], "output": output},
                    )
                job.update(state="succeeded", output=output)
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError) as error:
                logger.exception("Analysis failed for job %s", identifier)
                job.update(state="failed", error=str(error))
            job["finished_at"] = utc_now()
        self.current_job = None

    def get(self, job_id: str) -> dict[str, Any]:
        return self.jobs[job_id]

    async def close(self) -> None:
        self.closing = True
        if self.worker is not None:
            _, cancelled = await _settle(self.worker)
            if cancelled:
                raise asyncio.CancelledError

    def compute(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        request: AnalysisRequest = snapshot["request"]
        excluded = {"invalid": 0, "missing_prior": 0, "nonfinite": 0}
        digest = hashlib.sha256()
        records = snapshot["records"]
        revisions: set[int] = set()
        collected: list[tuple[str, AnalysisSample]] | deque[tuple[str, AnalysisSample]]
        collected = deque(maxlen=request.window_size) if request.mode == "live" else []

        def collect(run_id: str, raw: dict[str, Any]) -> None:
            sample = AnalysisSample.model_validate(raw)
            if not sample.valid:
                excluded["invalid"] += 1
            elif sample.expected_m is None:
                excluded["missing_prior"] += 1
            elif not all(
                math.isfinite(value) for value in (sample.raw_m, sample.corrected_m, sample.expected_m)
            ):
                excluded["nonfinite"] += 1
            else:
                if sample.prior_revision is not None:
                    revisions.add(sample.prior_revision)
                collected.append((run_id, sample))

        if records is not None:
            digest.update(json.dumps(records, sort_keys=True, allow_nan=False).encode())
            for run_id, raw in records:
                collect(run_id, raw)
            excluded["invalid"] = sum(run.get("excluded_count", 0) for run in snapshot["runs"])
        else:
            for run_id, path, size in snapshot["sources"]:
                with path.open("rb") as stream:
                    remaining = size
                    while remaining:
                        line = stream.readline(remaining)
                        if not line.endswith(b"\n"):
                            raise ValueError(f"{run_id}: incomplete recorded telemetry at snapshot boundary")
                        remaining -= len(line)
                        digest.update(line)
                        collect(run_id, json.loads(line))
        grouped: dict[str, list[AnalysisSample]] = {}
        for run_id, sample in collected:
            grouped.setdefault(run_id, []).append(sample)

        def arrays(samples: list[AnalysisSample]):
            return (
                np.array(
                    [getattr(sample, request.field) - sample.expected_m for sample in samples],
                    dtype=np.float64,
                ),
                np.array([sample.time_ms for sample in samples], dtype=np.int64),
                np.array([sample.seq for sample in samples], dtype=np.int64),
            )

        values, _, _ = arrays([sample for _, sample in collected])
        results = []
        for method in dict.fromkeys(request.methods):
            if method == "statistics":
                result = describe(values)
                if request.mode == "live":
                    self._full_moments(result, snapshot["runs"], request.field)
            elif method == "histogram":
                result = histogram(values, request.histogram_bins)
            elif method == "gmm":
                result = fit_gmm(values, request.components, request.seed)
            else:
                result = pooled_power_spectrum(
                    [(run_id, *arrays(samples)) for run_id, samples in grouped.items()]
                )
            results.append(
                {
                    "method": method,
                    "label": METHODS[method]["label"],
                    "run_id": None,
                    "result": result.model_dump(mode="json"),
                }
            )
        return {
            "included_count": len(collected),
            "excluded": excluded,
            "results": results,
            "mode": request.mode,
            "window_size": request.window_size if request.mode == "live" else None,
            "provenance": {
                "batch_id": request.batch_id,
                "batch_outcome": snapshot["batch"]["outcome"],
                "runs": [{"run_id": run["run_id"], "state": run["state"]} for run in snapshot["runs"]],
                "source_sha256": digest.hexdigest(),
                "prior_revisions": sorted(revisions),
                "field": request.field,
                "analysis_version": ANALYSIS_VERSION,
                "created_at": utc_now(),
                "libraries": {name: version(name) for name in ("numpy", "scipy", "scikit-learn")},
            },
        }

    @staticmethod
    def _full_moments(result: AnalysisResult, runs: list[dict[str, Any]], field: str) -> None:
        summaries = [run.get("summaries", {}).get(field) for run in runs]
        if not all(summary is not None for summary in summaries):
            return
        count = sum(summary["count"] for summary in summaries)
        if not count:
            return
        mean = sum(summary["count"] * (summary["bias_m"] or 0) for summary in summaries) / count
        m2 = sum(
            (summary["std_m"] or 0) ** 2 * max(0, summary["count"] - 1)
            + summary["count"] * ((summary["bias_m"] or 0) - mean) ** 2
            for summary in summaries
        )
        complete = {
            "count": count,
            "bias_m": mean,
            "std_m": math.sqrt(m2 / (count - 1)) if count > 1 else None,
            "mae_m": sum(summary["count"] * (summary["mae_m"] or 0) for summary in summaries) / count,
            "rmse_m": math.sqrt(
                sum(summary["count"] * (summary["rmse_m"] or 0) ** 2 for summary in summaries) / count
            ),
            "min_m": min(summary["min_m"] for summary in summaries if summary["count"]),
            "max_m": max(summary["max_m"] for summary in summaries if summary["count"]),
        }
        window_count = next((metric.value for metric in result.metrics if metric.key == "count"), 0)
        for metric in result.metrics:
            if metric.key in complete:
                metric.value = complete[metric.key]
                metric.label += " (full selection)"
            else:
                metric.label += " (window)"
        result.metrics.append(Metric(key="window_count", label="Live window samples", value=window_count))
