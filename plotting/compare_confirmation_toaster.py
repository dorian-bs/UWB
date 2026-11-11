"""Compare the DS-TWR noise histogram with and without the toaster, per distance.

Baseline = "confirmation" batch (for 1.2 m: "base 1.2", no confirmation exists).
Toaster  = "base <d> toaster" batch. Noise = raw_m - expected_m, pooled over all runs.
Left: histograms centred on their own mean so the noise shapes line up, with Gaussian fits
(sample mean / std). Right: the same data uncentred, with a double arrow between the two means
giving the bias shift (toaster - baseline). Raw data is only read.

Usage (from repo root):
    .venv/bin/python plotting/compare_confirmation_toaster.py [--out PATH] [--show]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from noise_vs_distance import find_batch, load_noise_mm

# (distance in m, baseline batch name, toaster batch name)
SELECTION = [
    (0.3, "base 0.3 confirmation", "base 0.3 toaster"),
    (0.5, "base 0.5 confirmation", "base 0.5 toaster"),
    (0.78, "base confirmation", "base 0.78 toaster"),
    (1.2, "base 1.2", "base 1.2 toaster"),
]
BASE_COLOR, TOASTER_COLOR = "#1f77b4", "#d62728"


def gaussian(x: np.ndarray, mu: float, sd: float) -> np.ndarray:
    return np.exp(-0.5 * ((x - mu) / sd) ** 2) / (sd * np.sqrt(2 * np.pi))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(Path(__file__).with_name("confirmation_vs_toaster.png")))
    parser.add_argument("--show", action="store_true", help="open an interactive window")
    args = parser.parse_args()

    data = {
        d: (load_noise_mm(find_batch(b, d), d), load_noise_mm(find_batch(t, d), d))
        for d, b, t in SELECTION
    }

    centred = [v - v.mean() for pair in data.values() for v in pair]
    lim = np.percentile(np.abs(np.concatenate(centred)), 99.5)
    bins = np.linspace(-lim, lim, 70)

    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    fig, grid = plt.subplots(
        len(data), 2, figsize=(12, 11), gridspec_kw={"width_ratios": [3, 1.4]}, sharey=True
    )
    axes, raw_axes = grid[:, 0], grid[:, 1]
    for ax in axes[1:]:
        ax.sharex(axes[0])
    for ax in raw_axes[1:]:
        ax.sharex(raw_axes[0])
    raw_all = np.concatenate([v for pair in data.values() for v in pair])
    raw_lo, raw_hi = raw_all.min(), raw_all.max()
    raw_bins = np.linspace(raw_lo, raw_hi, 70)
    grid = np.linspace(-lim, lim, 400)
    raw_grid = np.linspace(raw_lo, raw_hi, 800)
    fig.suptitle("Effect of the toaster on ranging noise", fontsize=15, weight="bold")

    for ax, raw_ax, (dist, (base, toaster)) in zip(axes, raw_axes, data.items()):
        ax.hist(base - base.mean(), bins=bins, density=True, color=BASE_COLOR, alpha=0.45, label="Baseline")
        ax.hist(toaster - toaster.mean(), bins=bins, density=True, color=TOASTER_COLOR, alpha=0.45,
                label="Toaster")
        for vals, color in ((base, BASE_COLOR), (toaster, TOASTER_COLOR)):
            mu, sd = vals.mean(), vals.std(ddof=1)
            ax.plot(grid, gaussian(grid, 0.0, sd), color=color, linewidth=2)
            raw_ax.plot(raw_grid, gaussian(raw_grid, mu, sd), color=color, linewidth=1.5)
            raw_ax.axvline(mu, color=color, linestyle="--", linewidth=1)
        ax.axvline(0, color="grey", linestyle=":", linewidth=1)
        ax.set_xlim(-lim, lim)
        ax.set_ylabel("Density")
        raw_ax.hist(base, bins=raw_bins, density=True, color=BASE_COLOR, alpha=0.45)
        raw_ax.hist(toaster, bins=raw_bins, density=True, color=TOASTER_COLOR, alpha=0.45)
        mu_base, mu_toaster = base.mean(), toaster.mean()
        blend = raw_ax.get_xaxis_transform()
        raw_ax.annotate("", xy=(mu_toaster, 0.8), xytext=(mu_base, 0.8), xycoords=blend, textcoords=blend,
                        arrowprops={"arrowstyle": "<->", "color": "black", "linewidth": 1.3,
                                    "shrinkA": 0, "shrinkB": 0})
        raw_ax.text((mu_base + mu_toaster) / 2, 0.83, f"{mu_toaster - mu_base:+.1f} mm", transform=blend,
                    ha="center", va="bottom", fontsize=11, weight="bold")
        raw_ax.axvline(0, color="grey", linestyle=":", linewidth=1)
        raw_ax.set_xlim(raw_lo, raw_hi)
        raw_ax.tick_params(labelleft=False)

        ax.text(0.015, 0.8, f"{dist} m", transform=ax.transAxes, weight="bold", fontsize=13)
    axes[0].plot([], [], color="black", linewidth=2, label="Gaussian fit")
    axes[0].set_ylim(0, axes[0].get_ylim()[1] * 1.25)
    axes[0].legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 0.7))
    axes[-1].set_xlabel("Range error, centred on each batch mean [mm]")
    raw_axes[0].set_title("Same data, not centred", fontsize=11)
    raw_axes[-1].set_xlabel("Range error [mm]")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(args.out)
    fig.savefig(out, dpi=200)
    print(f"Saved {out}")
    for dist, (base, toaster) in data.items():
        print(f"{dist:>5} m  delta bias = {toaster.mean() - base.mean():+.2f} mm")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
