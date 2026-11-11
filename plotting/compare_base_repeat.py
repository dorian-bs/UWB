"""Compare two identical-setup 0.5 m batches recorded at different times.

"base 0.5" vs "base 0.5 confirmation 2": nothing was touched between them, yet the noise shape
changed. Histogram outlines are uncentred, with a dashed line at each mean. Raw data is only read.

Usage (from repo root):
    .venv/bin/python plotting/compare_base_repeat.py [--out PATH] [--show]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from noise_vs_distance import find_batch, load_noise_mm

DISTANCE = 0.5
FIRST = ("base 0.5", "#1f77b4", "base measurement")
SECOND = ("base 0.5 confirmation 2", "#de601c", "confirmation measurement")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(Path(__file__).with_name("base_0.5_repeat.png")))
    parser.add_argument("--show", action="store_true", help="open an interactive window")
    args = parser.parse_args()

    series = [(label, color, load_noise_mm(find_batch(name, DISTANCE), DISTANCE))
              for name, color, label in (FIRST, SECOND)]

    all_vals = np.concatenate([v for _, _, v in series])
    bins = np.linspace(all_vals.min(), all_vals.max(), 70)

    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(9, 4.5))
    fig.suptitle(f"Same setup : two recordings, different noise shape (no clue what happened)",
                 fontsize=14, weight="bold")

    for label, color, vals in series:
        ax.hist(vals, bins=bins, density=True, histtype="step", linewidth=2, color=color, label=label)
        ax.axvline(vals.mean(), color=color, linestyle="--", linewidth=1)

    ax.axvline(0, color="grey", linestyle=":", linewidth=1)
    ax.set_xlim(bins[0], bins[-1])
    ax.set_ylim(0, ax.get_ylim()[1] * 1.15)
    ax.set_ylabel("Density")
    ax.set_xlabel("Range error [mm]")
    ax.legend(frameon=False, loc="upper left")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(args.out)
    fig.savefig(out, dpi=200)
    print(f"Saved {out}")
    for label, _, vals in series:
        print(f"{label:>26}  n={len(vals)}  mean={vals.mean():+.2f} mm  std={vals.std(ddof=1):.2f} mm")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
