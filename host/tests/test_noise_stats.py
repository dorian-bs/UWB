"""Noise-modelling toolkit: statistical tests must be calibrated, not just run."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from uwb_lab import noise_models as nm  # noqa: E402
from uwb_lab import noise_stats as ns  # noqa: E402
from uwb_lab.noise_data import parse_name  # noqa: E402


def ar1_runs(n_runs: int, n: int, phi: float, sigma: float, run_sd: float, seed: int, shift: float = 0.0):
    """Runs with an AR(1) within-run process plus an independent per-run offset (the cluster effect)."""
    rng = np.random.default_rng(seed)
    runs = []
    for _ in range(n_runs):
        e = rng.standard_normal(n) * sigma * np.sqrt(1 - phi**2)
        x = np.empty(n)
        x[0] = rng.standard_normal() * sigma
        for i in range(1, n):
            x[i] = phi * x[i - 1] + e[i]
        runs.append(x + shift + rng.standard_normal() * run_sd)
    return runs


def test_parse_name_extracts_orientation_and_obstacles():
    assert parse_name("base 1.25 tx + 180d rx + 90d confirmation") == {
        "env": "base", "tx_rot_deg": 180, "rx_rot_deg": 90, "obstacle": "none",
    }  # fmt: skip
    assert (
        parse_name("table 6 planche plastique et bois et toaster")["obstacle"]
        == "plastic_board+toaster+wood_board"
    )
    assert parse_name("table salavaux")["env"] == "salavaux"


def test_effective_sample_size_tracks_autocorrelation():
    (x,) = ar1_runs(1, 20000, 0.8, 1.0, 0.0, seed=1)
    # theory: tau = (1+phi)/(1-phi) = 9
    assert 20000 / 9 * 0.7 < ns.effective_sample_size(x) < 20000 / 9 * 1.4
    white = np.random.default_rng(2).standard_normal(5000)
    assert ns.effective_sample_size(white) > 0.8 * 5000
    assert ns.ljung_box(x)[1] < 1e-6 < 0.01 < ns.ljung_box(white)[1]


def test_adjust_pvalues():
    p = [0.01, 0.04, 0.03]
    assert np.allclose(ns.adjust_pvalues(p, "holm"), [0.03, 0.06, 0.06])
    assert np.allclose(ns.adjust_pvalues(p, "bh"), [0.03, 0.04, 0.04])


def test_cluster_permutation_is_calibrated_under_null_and_detects_shift():
    p_null = []
    for seed in range(30):
        a = ar1_runs(10, 300, 0.5, 0.01, 0.003, seed=100 + seed)
        b = ar1_runs(10, 300, 0.5, 0.01, 0.003, seed=200 + seed)
        p_null.append(ns.cluster_permutation_test(a, b, n_perm=500, shape=False, seed=seed)["p_mean_diff"])
    assert np.mean(np.array(p_null) < 0.05) < 0.2  # nominal 5%, allow Monte-Carlo slack

    a = ar1_runs(10, 300, 0.5, 0.01, 0.003, seed=1)
    b = ar1_runs(10, 300, 0.5, 0.01, 0.003, seed=2, shift=0.02)
    r = ns.compare_datasets(a, b, n_perm=1000, n_boot=1000)
    assert r["p_mean_diff"] < 0.01 and r["welch_p_mean"] < 0.01
    lo, hi = r["mean_diff_lo"], r["mean_diff_hi"]
    assert lo < 0.02 < hi


def test_cluster_test_is_stricter_than_naive_pooling_with_run_offsets():
    # Large run-to-run offsets and no real effect: pooling all samples would claim significance.
    a = ar1_runs(8, 500, 0.3, 0.005, 0.01, seed=7)
    b = ar1_runs(8, 500, 0.3, 0.005, 0.01, seed=8)
    pooled_p = ns.stats.ttest_ind(np.concatenate(b), np.concatenate(a), equal_var=False).pvalue
    cluster_p = ns.cluster_permutation_test(a, b, n_perm=2000, shape=False)["p_mean_diff"]
    assert cluster_p > pooled_p


def test_scale_and_shape_effects():
    rng = np.random.default_rng(0)
    a = [rng.normal(0, 0.01, 400) for _ in range(10)]
    b = [rng.normal(0, 0.02, 400) for _ in range(10)]
    r = ns.cluster_permutation_test(a, b, n_perm=500)
    assert r["p_log_std_ratio"] < 0.01 and abs(r["log_std_ratio"] - np.log(2)) < 0.1
    heavy = [rng.standard_t(3, 400) * 0.01 for _ in range(10)]
    same_scale = [rng.normal(0, 0.01, 400) for _ in range(10)]
    assert ns.cluster_permutation_test(same_scale, heavy, n_perm=300)["p_shape_ks"] < 0.05


def test_variance_components_recovers_batch_effect():
    rng = np.random.default_rng(3)
    batches = {}
    for i in range(6):
        offset = rng.normal(0, 0.01)
        batches[f"b{i}"] = [offset + rng.normal(0, 0.003) + rng.normal(0, 0.01, 300) for _ in range(10)]
    vc = ns.variance_components(batches)
    assert 0.005 < vc["sd_between_batch_m"] < 0.02
    assert vc["within_run"] == pytest.approx(0.01**2, rel=0.2)


def test_minimum_detectable_difference_roundtrip():
    delta = ns.minimum_detectable_difference(0.01, 10, 10)
    assert ns.units_needed(delta, 0.01) in (10, 11)


def test_family_comparison_prefers_heavy_tails_for_student_t_data():
    x = np.random.default_rng(0).standard_t(3, 6000) * 0.01
    table = ns.compare_families(x)
    assert table.loc[0, "family"] in {"student_t", "laplace", "gmm2"}
    assert table.set_index("family").loc["normal", "d_aic"] > 50


def synthetic_samples() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for b, (d, rot) in enumerate(
        [(0.3, 0), (0.5, 0), (0.8, 0), (1.2, 0), (0.3, 90), (0.8, 90), (1.2, 90)] * 2
    ):
        n = 400
        err = 0.05 * (rot == 90) + 0.01 * d + rng.standard_t(5, n) * 0.005 * (1 + d)
        rows.append(pd.DataFrame({"batch_id": f"b{b}", "distance_m": d, "rot": str(rot), "err_m": err}))
    return pd.concat(rows, ignore_index=True)


def test_scoreboard_rewards_models_that_know_the_conditions():
    samples = synthetic_samples()
    summary, per_batch = nm.scoreboard(
        {
            "global": lambda: nm.LookupModel(),
            "parametric": lambda: nm.ParametricModel(categorical=["rot"], numeric=["distance_m"]),
        },
        samples,
    )
    assert summary.index[0] == "parametric"
    assert summary.loc["parametric", "mean_ll"] > summary.loc["global", "mean_ll"] + 0.5
    assert 0.8 < summary.loc["parametric", "cover90"] < 0.97
    assert set(per_batch["model"]) == {"global", "parametric"}


def test_simulated_runs_keep_marginal_and_autocorrelation():
    samples = synthetic_samples()
    model = nm.ParametricModel(categorical=["rot"]).fit(samples)
    runs = nm.simulate_runs(model, {"distance_m": 0.8, "rot": "90"}, n_runs=4, n_samples=3000, phi=0.6)
    loc, scale = model.predict(pd.DataFrame([{"distance_m": 0.8, "rot": "90", "err_m": 0}]))  # tau == 0 here
    pooled = np.concatenate(runs)
    assert pooled.mean() == pytest.approx(loc[0], abs=0.002)
    assert pooled.std() == pytest.approx(scale[0] * model.law.std(), rel=0.15)
    assert nm.estimate_phi(runs) == pytest.approx(0.6, abs=0.1)


def test_batch_effect_splits_between_and_within_batch_spread():
    rng = np.random.default_rng(5)
    frames = []
    for b in range(
        12
    ):  # same condition, each batch has an unexplained offset (sd 20 mm), within-batch sd 5 mm
        offset = rng.normal(0, 0.02)
        frames.append(
            pd.DataFrame(
                {"batch_id": f"b{b}", "distance_m": 0.5, "err_m": offset + rng.normal(0, 0.005, 500)}
            )
        )
    samples = pd.concat(frames, ignore_index=True)
    model = nm.LookupModel(heavy_tail=False, batch_col="batch_id").fit(samples)
    loc, scale = model.predict(samples.iloc[:1])
    _, within = model._predict(samples.iloc[:1])
    assert model.tau == pytest.approx(0.02, rel=0.5)
    assert within[0] == pytest.approx(0.005, rel=0.1)
    assert scale[0] == pytest.approx(np.hypot(model.tau, 0.005), rel=0.01)
    batch_means = [
        np.concatenate(nm.simulate_runs(model, {"distance_m": 0.5}, 5, 50, seed=k, batch_effect=True)).mean()
        for k in range(30)
    ]
    assert np.std(batch_means) == pytest.approx(
        model.tau, rel=0.5
    )  # each simulated batch gets its own offset
