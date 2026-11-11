"""Compare the DS-TWR noise histogram at several distances (same environment).

Uses the "base ... confirmation" experiments (0.3, 0.5, 0.78 m) and "base 1.2" (1.2 m,
no confirmation batch exists). Noise = raw_m - expected_m, pooled over all runs of a batch.
Raw data is only read, never modified.

Usage (from repo root):
    .venv/bin/python plotting/noise_vs_distance.py [--out plotting/noise_vs_distance.png]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "data" / "sessions" / "experiments"

# (distance in m, experiment name as recorded in manifest.json)
SELECTION = [
    (0.3, "base 0.3 confirmation"),
    (0.5, "base 0.5 confirmation"),
    (0.78, "base confirmation"),
    (1.2, "base 1.2"),
]
COLORS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a"]


def find_batch(name: str, distance: float) -> Path:
    matches = []
    for manifest in EXPERIMENTS.glob("*/manifest.json"):
        data = json.loads(manifest.read_text())
        prior = data.get("prior", {})
        if data["config"]["name"].strip() == name and prior.get("expected_distance_m") == distance:
            matches.append(manifest.parent)
    if len(matches) != 1:
        raise SystemExit(f"Expected one batch named {name!r} at {distance} m, found {len(matches)}")
    return matches[0]


def load_noise_mm(batch: Path, expected_m: float) -> np.ndarray:
    values = []
    for telemetry in sorted(batch.glob("runs/*/telemetry.ndjson")):
        for line in telemetry.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "range" and rec.get("valid") and "raw_m" in rec:
                values.append((rec["raw_m"] - expected_m) * 1000.0)
    return np.asarray(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(Path(__file__).with_name("noise_vs_distance.png")))
    parser.add_argument("--show", action="store_true", help="open an interactive window")
    args = parser.parse_args()

    data = {d: load_noise_mm(find_batch(n, d), d) for d, n in SELECTION}

    lim = np.percentile(np.abs(np.concatenate(list(data.values()))), 99.5)
    bins = np.linspace(-lim, lim, 70)
    grid = np.linspace(-lim, lim, 400)

    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    fig, (ax_top, *axes) = plt.subplots(5, 1, figsize=(9, 12), gridspec_kw={"height_ratios": [2.2, 1, 1, 1, 1]})
    fig.suptitle("Ranging noise vs. Distance and Gaussian fit", fontsize=15, weight="bold")

    for (dist, vals), color in zip(data.items(), COLORS):
        label = f"{dist} m"
        ax_top.hist(vals, bins=bins, density=True, histtype="step", linewidth=2, color=color, label=label)
    ax_top.set_xlabel("Range error [mm]")
    ax_top.set_ylabel("Density [1/mm]")
    ax_top.set_xlim(-lim, lim)
    ax_top.axvline(0, color="grey", linestyle=":", linewidth=1)
    ax_top.set_ylim(0, ax_top.get_ylim()[1] * 1.3)
    ax_top.legend(frameon=False, ncol=1, loc="upper left")

    ymax = 0.0
    for ax, (dist, vals), color in zip(axes, data.items(), COLORS):
        mu, sd = vals.mean(), vals.std(ddof=1)
        ax.hist(vals, bins=bins, density=True, color=color, alpha=0.75, edgecolor="white", linewidth=0.4)
        ax.plot(grid, np.exp(-0.5 * ((grid - mu) / sd) ** 2) / (sd * np.sqrt(2 * np.pi)),
                color="black", linewidth=1.2)
        ax.axvline(0, color="grey", linestyle=":", linewidth=1)
        ax.axvline(mu, color="black", linestyle="--", linewidth=1)
        ax.set_xlim(-lim, lim)
        ax.set_ylabel("Density")
        ax.text(0.015, 0.8, f"{dist} m", transform=ax.transAxes, weight="bold", color=color, fontsize=13)
        ax.text(0.985, 0.8, f"bias {mu:+.1f} mm   σ {sd:.1f} mm   n = {len(vals)}",
                transform=ax.transAxes, ha="right", fontsize=10)
        ymax = max(ymax, ax.get_ylim()[1])
    for ax in axes:
        ax.set_ylim(0, ymax)
    axes[0].legend(frameon=False, loc="upper center")
    axes[-1].set_xlabel("Range error [mm]")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(args.out)
    fig.savefig(out, dpi=200)
    print(f"Saved {out}")
    for dist, vals in data.items():
        print(f"{dist:>5} m  n={len(vals):5d}  bias={vals.mean():+7.2f} mm  std={vals.std(ddof=1):6.2f} mm")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
