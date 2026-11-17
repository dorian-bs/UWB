"""Load recorded experiment batches into pandas for noise modelling (read-only, hardware-free).

Hierarchy used everywhere: batch (one experiment, one prior) > run (independently restarted
capture) > sample (one DS-TWR exchange). Raw data is never modified: ``err_m = raw_m - distance_m``
is derived here, ``raw_m`` stays untouched.

Experimental conditions are not recorded structurally, only in the free-text batch name, so
``seed_conditions`` writes a hand-editable ``notebooks/conditions.csv`` once and never overwrites it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "data" / "sessions" / "experiments"
CONDITIONS_CSV = ROOT / "notebooks" / "conditions.csv"

DIAGNOSTICS = ["rx_power_dbm", "fp_power_dbm", "fp_gap_db"]
CONDITION_COLUMNS = ["batch_id", "name", "env", "tx_rot_deg", "rx_rot_deg", "obstacle", "include", "notes"]
RECORD_COLUMNS = ["v", "seq", "time_ms", "raw_m", "valid"]  # present in every telemetry version
RADIO_COLUMNS = ["channel", "preamble", "data_rate", "tx_power", "period_ms"]


def parse_name(name: str) -> dict:
    """Best-effort conditions from a batch name; the CSV is the source of truth after seeding."""
    n = name.lower().strip()
    tx = re.search(r"tx\s*\+\s*(\d+)\s*d", n)
    rx = re.search(r"rx\s*\+\s*(\d+)\s*d", n)
    obstacles = [
        label
        for label, pattern in [("plastic_board", "plastique"), ("toaster", "toaster"), ("wood_board", "bois")]
        if pattern in n
    ]
    if n.startswith("base"):
        env = "base"
    elif "salavaux" in n:
        env = "salavaux"
    elif m := re.match(r"table\s*(\d+)", n):
        env = f"table{m.group(1)}"
    elif "static los" in n:
        env = "static-los"
    else:
        env = "unknown"
    return {
        "env": env,
        "tx_rot_deg": int(tx.group(1)) if tx else 0,
        "rx_rot_deg": int(rx.group(1)) if rx else 0,
        "obstacle": "+".join(sorted(obstacles)) or "none",
    }


def _read_manifests(experiments: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(experiments.glob("*/manifest.json"))]


def load_batches(experiments: Path = EXPERIMENTS) -> pd.DataFrame:
    """One row per batch with prior distance and the radio configuration snapshotted at batch start."""
    rows = []
    for m in _read_manifests(experiments):
        tx = ((m.get("radio") or {}).get("boards") or {}).get("tx", {}).get("state") or {}
        prior = m.get("prior") or {}
        rows.append(
            {
                "batch_id": m["batch_id"],
                "started_at": pd.Timestamp(m["started_at"]),
                "name": m["config"]["name"].strip(),
                "outcome": m.get("outcome"),
                "distance_m": prior.get("expected_distance_m"),
                "prior_uncertainty_m": prior.get("uncertainty_m"),
                "channel": tx.get("channel"),
                "preamble": tx.get("preamble"),
                "data_rate": tx.get("data_rate"),
                "tx_power": hex(tx["tx_power"]) if tx.get("tx_power") else None,
                "period_ms": tx.get("period_ms"),
                "tx_ant_dly": tx.get("tx_ant_dly"),
                "n_runs_planned": m["config"].get("run_count"),
                "duration_s": m["config"].get("duration_s"),
            }
        )
    return pd.DataFrame(rows).sort_values("started_at").reset_index(drop=True)


def seed_conditions(batches: pd.DataFrame, path: Path = CONDITIONS_CSV) -> pd.DataFrame:
    """Return the conditions table, appending (never overwriting) rows for batches not yet listed."""
    known = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=CONDITION_COLUMNS)
    new = []
    for b in batches.itertuples():
        if b.batch_id in set(known["batch_id"]):
            continue
        new.append(
            {
                "batch_id": b.batch_id,
                "name": b.name,
                **parse_name(b.name),
                "include": b.outcome == "completed",
                "notes": "",
            }
        )
    if new or not path.exists():
        known = pd.concat([known, pd.DataFrame(new, columns=CONDITION_COLUMNS)], ignore_index=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        known.to_csv(path, index=False)
    known["include"] = known["include"].astype(bool)
    return known


def load_samples(
    batch_ids: list[str] | None = None, experiments: Path = EXPERIMENTS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(runs, samples)`` for completed runs. Samples keep only valid range records.

    ``t_s`` is device time since the first sample of the run; ``err_m`` is ``raw_m - distance_m``.
    """
    run_rows, frames = [], []
    for m in _read_manifests(experiments):
        if batch_ids is not None and m["batch_id"] not in batch_ids:
            continue
        distance = (m.get("prior") or {}).get("expected_distance_m")
        for r in m["runs"]:
            path = experiments / m["batch_id"] / (r.get("session") or "") / "telemetry.ndjson"
            if r["state"] != "completed" or not r.get("session") or not path.exists():
                continue
            records = []
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if line.startswith('{"type":"range"'):
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            if not records:
                continue
            df = pd.DataFrame(records).reindex(columns=[*RECORD_COLUMNS, *DIAGNOSTICS, "ipatov_power"])
            n_raw = len(df)
            df = df[df["valid"].astype(bool)].copy()
            df["batch_id"], df["run_id"] = m["batch_id"], r["run_id"]
            df["t_s"] = (df["time_ms"] - df["time_ms"].iloc[0]) / 1000.0
            df["err_m"] = df["raw_m"] - distance
            frames.append(df)
            run_rows.append(
                {
                    "batch_id": m["batch_id"],
                    "run_id": r["run_id"],
                    "n_valid": len(df),
                    "valid_ratio": len(df) / n_raw,
                    "yield_pct": r.get("sample_yield_pct"),
                    "diag_coverage": float(df[DIAGNOSTICS[0]].notna().mean()) if len(df) else 0.0,
                }
            )
    keep = ["batch_id", "run_id", "v", "seq", "t_s", "raw_m", "err_m", *DIAGNOSTICS, "ipatov_power"]
    samples = pd.concat(frames, ignore_index=True)
    samples = samples[[c for c in keep if c in samples.columns]]
    return pd.DataFrame(run_rows), samples


def build_dataset(
    experiments: Path = EXPERIMENTS, conditions_csv: Path = CONDITIONS_CSV
) -> dict[str, pd.DataFrame]:
    """Everything the notebook needs: batches (+conditions), runs and samples joined to conditions.

    ``cond`` is the replicate-group key (same physical setup), ``replicate`` ranks batches of one
    ``cond`` by start time. Rows of batches with ``include == False`` are dropped from runs/samples.
    """
    batches = load_batches(experiments)
    conditions = seed_conditions(batches, conditions_csv)
    batches = batches.merge(conditions.drop(columns=["name"]), on="batch_id", how="left")
    batches["include"] = batches["include"].fillna(False).astype(bool)
    batches["cond"] = (
        batches["env"].astype(str) + "|d" + batches["distance_m"].astype(str)
        + "|tx" + batches["tx_rot_deg"].astype(str) + "|rx" + batches["rx_rot_deg"].astype(str)
        + "|" + batches["obstacle"].astype(str)
    )  # fmt: skip
    batches["replicate"] = batches.groupby("cond")["started_at"].rank(method="first").astype(int)
    included = batches.loc[batches["include"], "batch_id"].tolist()
    runs, samples = load_samples(included, experiments)
    batch_cols = ["batch_id", "name", "cond", "replicate", "env", "distance_m", "tx_rot_deg", "rx_rot_deg",
                  "obstacle", *RADIO_COLUMNS]  # fmt: skip
    runs = runs.merge(batches[batch_cols], on="batch_id")
    samples = samples.merge(batches[batch_cols], on="batch_id")
    for df in (runs, samples):
        for col in ("batch_id", "run_id", "cond", "env", "obstacle", "name"):
            df[col] = df[col].astype("category")
    return {"batches": batches, "runs": runs, "samples": samples}


def quality_gates(runs: pd.DataFrame, min_samples: int = 100, min_diag: float = 0.95) -> pd.DataFrame:
    """Flag runs failing the campaign gates from docs/EXPERIMENTS_AND_VALIDATION.md (diag coverage >= 95%)."""
    out = runs.copy()
    out["ok_samples"] = out["n_valid"] >= min_samples
    out["ok_diag"] = out["diag_coverage"] >= min_diag
    return out
