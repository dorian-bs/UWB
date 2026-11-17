"""Statistical tools for comparing DS-TWR noise datasets (numpy/scipy only, hardware-free).

Design rule: samples inside a run are autocorrelated and runs inside a batch share a setup, so the
*independent* unit is the run (and, for session-level claims, the batch). Every test here either
works on run-level summaries or resamples/permutes whole runs ("cluster" resampling). Never treat
the ~3000 samples of a batch as independent draws.

A "dataset" is a ``list[np.ndarray]``: one array of errors (metres) per run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

Runs = Sequence[np.ndarray]


# ---------------------------------------------------------------------------------- descriptive


def mad_sigma(x: np.ndarray) -> float:
    """Robust std estimate: 1.4826 * median absolute deviation."""
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def acf(x: np.ndarray, nlags: int) -> np.ndarray:
    """Biased sample autocorrelation for lags 0..nlags (FFT)."""
    x = np.asarray(x, float) - np.mean(x)
    n = len(x)
    nlags = min(nlags, n - 1)
    f = np.fft.rfft(x, 2 * n)
    r = np.fft.irfft(f * np.conj(f))[: nlags + 1]
    return r / r[0] if r[0] > 0 else np.full(nlags + 1, np.nan)


def effective_sample_size(x: np.ndarray, max_lag: int = 200) -> float:
    """ESS = n / tau, tau from Geyer's initial positive sequence (pairs of autocorrelations)."""
    n = len(x)
    rho = acf(x, min(max_lag, n - 1))
    if np.isnan(rho).any() or len(rho) < 3:
        return float("nan")
    m = len(rho) // 2 * 2
    pairs = rho[:m:2] + rho[1:m:2]
    total = 0.0
    for g in pairs:
        if g <= 0:
            break
        total += g
    tau = max(-1.0 + 2.0 * total, 1.0 / n)
    return float(n / tau)


def ljung_box(x: np.ndarray, lags: int = 10) -> tuple[float, float]:
    """Ljung-Box Q statistic and p-value; small p means the series is not white noise."""
    n = len(x)
    rho = acf(x, lags)[1:]
    k = np.arange(1, len(rho) + 1)
    q = float(n * (n + 2) * np.sum(rho**2 / (n - k)))
    return q, float(stats.chi2.sf(q, len(rho)))


def allan_deviation(runs: Runs, taus: Sequence[int]) -> np.ndarray:
    """Non-overlapping Allan deviation (m), averaging the Allan *variance* over runs, per tau in samples."""
    out = []
    for m in taus:
        var, weight = 0.0, 0
        for x in runs:
            n = len(x) // m
            if n < 3:
                continue
            means = np.asarray(x[: n * m]).reshape(n, m).mean(axis=1)
            var += 0.5 * np.mean(np.diff(means) ** 2) * (n - 1)
            weight += n - 1
        out.append(np.sqrt(var / weight) if weight else np.nan)
    return np.array(out)


def run_summary(
    samples: pd.DataFrame, value: str = "err_m", keys: Sequence[str] = ("batch_id", "run_id")
) -> pd.DataFrame:
    """One row per run: location, scale, shape and temporal-dependence summaries."""
    rows = []
    for key, g in samples.groupby(list(keys), observed=True, sort=False):
        x = g[value].to_numpy(float)
        if len(x) < 8:
            continue
        rows.append(
            {
                **dict(zip(keys, key, strict=True)),
                "n": len(x),
                "mean": x.mean(),
                "std": x.std(ddof=1),
                "mad_sigma": mad_sigma(x),
                "skew": stats.skew(x),
                "ex_kurt": stats.kurtosis(x),
                "p95_abs_dev": np.percentile(np.abs(x - np.median(x)), 95),
                "lag1": acf(x, 1)[1],
                "ess": effective_sample_size(x),
            }
        )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------ effect sizes


def hedges_g(a: np.ndarray, b: np.ndarray) -> float:
    """Standardised mean difference b - a with small-sample correction (use on run-level values)."""
    na, nb = len(a), len(b)
    sp = np.sqrt(((na - 1) * np.var(a, ddof=1) + (nb - 1) * np.var(b, ddof=1)) / (na + nb - 2))
    return float((np.mean(b) - np.mean(a)) / sp * (1 - 3 / (4 * (na + nb) - 9))) if sp > 0 else float("nan")


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """P(b > a) - P(b < a) in [-1, 1]; non-parametric, robust to outliers (run-level values)."""
    u = stats.mannwhitneyu(b, a, alternative="two-sided").statistic
    return float(2 * u / (len(a) * len(b)) - 1)


def adjust_pvalues(p: Sequence[float], method: str = "holm") -> np.ndarray:
    """Multiple-comparison correction: ``holm`` (family-wise error) or ``bh`` (false discovery rate)."""
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    if method == "holm":
        running = 0.0
        for rank, i in enumerate(order):
            running = max(running, (m - rank) * p[i])
            adj[i] = min(1.0, running)
    elif method == "bh":
        running = 1.0
        for rank in range(m - 1, -1, -1):
            i = order[rank]
            running = min(running, p[i] * m / (rank + 1))
            adj[i] = running
    else:
        raise ValueError(f"unknown method {method!r}")
    return adj


# ------------------------------------------------------------------ cluster (run-level) inference


def _sufficient(runs: Runs) -> np.ndarray:
    """Per-run (n, sum, sum of squares), enough to get pooled mean/std of any run subset."""
    return np.array([[len(x), np.sum(x), np.sum(np.square(x))] for x in runs], float)


def _mean_std(suff: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pooled mean and std for stacks of run subsets; ``suff`` has shape (..., 3) summed over runs."""
    n, s, ss = suff[..., 0], suff[..., 1], suff[..., 2]
    mean = s / n
    var = np.maximum((ss - n * mean**2) / (n - 1), 0.0)
    return mean, np.sqrt(var)


def _effects(sa: np.ndarray, sb: np.ndarray) -> dict[str, np.ndarray]:
    ma, sda = _mean_std(sa)
    mb, sdb = _mean_std(sb)
    return {"mean_diff": mb - ma, "log_std_ratio": np.log(sdb / sda)}


def cluster_permutation_test(
    a: Runs, b: Runs, n_perm: int = 5000, shape: bool = True, seed: int = 0, shape_max_samples: int = 6000
) -> dict[str, float]:
    """Permute whole runs between datasets A and B and compare to the observed effects.

    Effects: ``mean_diff`` (B - A, bias shift), ``log_std_ratio`` (log sigma_B/sigma_A, noise-scale
    change) and, if ``shape``, ``shape_ks`` (KS distance after removing each dataset's median and
    MAD-sigma, i.e. a change of distribution *shape* beyond location/scale). Two-sided Monte-Carlo p.
    The shape statistic is the slow one: runs are thinned (every k-th sample) so a dataset has at most
    ``shape_max_samples`` samples, and at most 500 permutations are used for it.

    Assumption: under H0 runs are exchangeable between A and B. If A and B are different batches,
    a batch effect (setup drift) breaks that; calibrate with ``aa_effects`` on replicate batches.
    """
    rng = np.random.default_rng(seed)
    runs = [*a, *b]
    na, nr = len(a), len(a) + len(b)
    suff = _sufficient(runs)
    obs = _effects(suff[:na].sum(axis=0), suff[na:].sum(axis=0))
    perms = np.argsort(rng.random((n_perm, nr)), axis=1)
    pa = suff[perms[:, :na]].sum(axis=1)
    pb = suff[perms[:, na:]].sum(axis=1)
    null = _effects(pa, pb)
    out = {k: float(obs[k]) for k in obs}
    for k in obs:
        out[f"p_{k}"] = float((1 + np.sum(np.abs(null[k]) >= abs(obs[k]) - 1e-15)) / (1 + n_perm))
    if shape:
        n_shape = min(n_perm, 500)
        stride = max(1, int(np.ceil(max(sum(map(len, a)), sum(map(len, b))) / shape_max_samples)))
        thin = [np.asarray(r)[::stride] for r in runs]

        def ks(ia: np.ndarray, ib: np.ndarray) -> float:
            xa = np.concatenate([thin[i] for i in ia])
            xb = np.concatenate([thin[i] for i in ib])
            return float(
                stats.ks_2samp(
                    (xa - np.median(xa)) / mad_sigma(xa), (xb - np.median(xb)) / mad_sigma(xb)
                ).statistic
            )

        d_obs = ks(np.arange(na), np.arange(na, nr))
        d_null = np.array([ks(p[:na], p[na:]) for p in perms[:n_shape]])
        out["shape_ks"] = d_obs
        out["p_shape_ks"] = float((1 + np.sum(d_null >= d_obs)) / (1 + n_shape))
    return out


def cluster_bootstrap_ci(
    a: Runs, b: Runs, n_boot: int = 5000, level: float = 0.95, seed: int = 0
) -> dict[str, tuple[float, float]]:
    """Percentile CIs for ``mean_diff`` and ``log_std_ratio`` resampling whole runs within each dataset."""
    rng = np.random.default_rng(seed)
    sa, sb = _sufficient(a), _sufficient(b)
    ia = rng.integers(0, len(a), (n_boot, len(a)))
    ib = rng.integers(0, len(b), (n_boot, len(b)))
    boot = _effects(sa[ia].sum(axis=1), sb[ib].sum(axis=1))
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    return {k: (float(np.quantile(v, lo)), float(np.quantile(v, hi))) for k, v in boot.items()}


def compare_datasets(
    a: Runs, b: Runs, n_perm: int = 5000, n_boot: int = 5000, seed: int = 0
) -> dict[str, float]:
    """Full A-vs-B report: effects with cluster-bootstrap CIs, cluster-permutation p-values, run-level tests.

    ``mean_diff`` / ``log_std_ratio`` / ``shape_ks`` come from cluster resampling (the primary evidence).
    ``welch_p_mean``, ``mwu_p_mean``, ``bf_p_std`` are naive run-level cross-checks (Welch t on run means,
    Mann-Whitney on run means, Brown-Forsythe on run stds); they should agree with the permutation tests.
    """
    report = cluster_permutation_test(a, b, n_perm=n_perm, seed=seed)
    ci = cluster_bootstrap_ci(a, b, n_boot=n_boot, seed=seed)
    for k, (lo, hi) in ci.items():
        report[f"{k}_lo"], report[f"{k}_hi"] = lo, hi
    ma = np.array([np.mean(x) for x in a])
    mb = np.array([np.mean(x) for x in b])
    sa = np.array([np.std(x, ddof=1) for x in a])
    sb = np.array([np.std(x, ddof=1) for x in b])
    report.update(
        {
            "n_runs_a": len(a),
            "n_runs_b": len(b),
            "n_a": int(sum(map(len, a))),
            "n_b": int(sum(map(len, b))),
            "sigma_a": float(np.std(np.concatenate(a), ddof=1)),
            "sigma_b": float(np.std(np.concatenate(b), ddof=1)),
            "hedges_g_run_means": hedges_g(ma, mb),
            "cliffs_delta_run_means": cliffs_delta(ma, mb),
            "welch_p_mean": float(stats.ttest_ind(mb, ma, equal_var=False).pvalue),
            "mwu_p_mean": float(stats.mannwhitneyu(mb, ma).pvalue),
            "bf_p_std": float(stats.levene(sb, sa, center="median").pvalue),
        }
    )
    return report


def pairwise_compare(
    datasets: dict[str, Runs],
    pairs: Sequence[tuple[str, str]],
    correction: str = "holm",
    n_perm: int = 3000,
    n_boot: int = 3000,
    seed: int = 0,
) -> pd.DataFrame:
    """``compare_datasets`` over many (A, B) pairs, with multiple-comparison-adjusted p-values."""
    rows = []
    for i, (ka, kb) in enumerate(pairs):
        rows.append(
            {"a": ka, "b": kb, **compare_datasets(datasets[ka], datasets[kb], n_perm, n_boot, seed + i)}
        )
    df = pd.DataFrame(rows)
    for col in ("p_mean_diff", "p_log_std_ratio", "p_shape_ks"):
        df[f"{col}_adj"] = adjust_pvalues(df[col].to_numpy(), correction)
    return df


# ------------------------------------------------------------------------ noise floor and power


def aa_effects(batches: dict[str, Runs]) -> pd.DataFrame:
    """Effects between *replicate* batches of the same physical setup (A/A comparisons).

    Any real condition effect must clearly exceed this batch-to-batch floor, otherwise the "effect" is
    indistinguishable from re-doing the same experiment on another day/placement.
    """
    keys = list(batches)
    rows = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1 :]:
            e = _effects(_sufficient(batches[ka]).sum(axis=0), _sufficient(batches[kb]).sum(axis=0))
            rows.append(
                {
                    "a": ka,
                    "b": kb,
                    "mean_diff": float(e["mean_diff"]),
                    "log_std_ratio": float(e["log_std_ratio"]),
                }
            )
    return pd.DataFrame(rows)


def variance_components(batches: dict[str, Runs]) -> dict[str, float]:
    """Method-of-moments nested variance decomposition for one setup: within-run, between-run, between-batch.

    Needs >= 2 replicate batches. Components are in m^2 and clipped at 0; with only a handful of
    batches the between-batch term is a rough estimate, report it with that caveat.
    """
    runs_per_batch = np.array([len(r) for r in batches.values()], float)
    all_runs = [x for r in batches.values() for x in r]
    n_bar = float(np.mean([len(x) for x in all_runs]))
    within = float(
        np.sum([(len(x) - 1) * np.var(x, ddof=1) for x in all_runs]) / np.sum([len(x) - 1 for x in all_runs])
    )
    run_var = np.mean([np.var([x.mean() for x in r], ddof=1) for r in batches.values() if len(r) > 1])
    between_run = max(float(run_var) - within / n_bar, 0.0)
    batch_means = np.array([np.mean([x.mean() for x in r]) for r in batches.values()])
    r_bar = float(np.mean(runs_per_batch))
    between_batch = max(float(np.var(batch_means, ddof=1)) - (between_run + within / n_bar) / r_bar, 0.0)
    total = within + between_run + between_batch
    return {
        "within_run": within,
        "between_run": between_run,
        "between_batch": between_batch,
        "frac_between_batch": between_batch / total,
        "sd_between_batch_m": float(np.sqrt(between_batch)),
    }


def minimum_detectable_difference(
    sd: float, n_a: int, n_b: int, alpha: float = 0.05, power: float = 0.8
) -> float:
    """Smallest mean difference detectable at ``alpha``/``power`` for run- (or batch-) level SD ``sd``."""
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    return float(z * sd * np.sqrt(1 / n_a + 1 / n_b))


def units_needed(delta: float, sd: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Units (runs or batches) *per group* needed to detect a mean difference ``delta``."""
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    return int(np.ceil(2 * (z * sd / delta) ** 2))


# --------------------------------------------------------------------------- distribution fits


class _Mixture:
    """Two-component Gaussian mixture exposing the same ``logpdf`` as a scipy frozen distribution."""

    def __init__(self, x: np.ndarray, seed: int = 0) -> None:
        from sklearn.mixture import GaussianMixture

        self.gm = GaussianMixture(2, random_state=seed, n_init=3, reg_covar=1e-10).fit(x.reshape(-1, 1))

    def logpdf(self, x: np.ndarray) -> np.ndarray:
        return self.gm.score_samples(np.asarray(x).reshape(-1, 1))

    def cdf(self, x: np.ndarray) -> np.ndarray:
        w, m, s = self.gm.weights_, self.gm.means_.ravel(), np.sqrt(self.gm.covariances_.ravel())
        return sum(wi * stats.norm.cdf(x, mi, si) for wi, mi, si in zip(w, m, s, strict=True))


FAMILIES: dict[str, tuple[object, int]] = {
    "normal": (stats.norm, 2),
    "laplace": (stats.laplace, 2),
    "student_t": (stats.t, 3),
    "skew_normal": (stats.skewnorm, 3),
    "shifted_lognormal": (stats.lognorm, 3),
    "gmm2": (None, 5),
}


def fit_family(name: str, x: np.ndarray, seed: int = 0):
    """Maximum-likelihood fit; returns an object with ``logpdf`` (and ``cdf``)."""
    if name == "gmm2":
        return _Mixture(x, seed)
    dist, _ = FAMILIES[name]
    return dist(*dist.fit(x))


def compare_families(x: np.ndarray, max_samples: int = 20000, seed: int = 0) -> pd.DataFrame:
    """AIC/BIC/KS for each candidate family on one dataset (in-sample; exploratory, see ``cv_families``)."""
    rng = np.random.default_rng(seed)
    x = np.asarray(x, float)
    if len(x) > max_samples:
        x = rng.choice(x, max_samples, replace=False)
    rows = []
    for name, (_, k) in FAMILIES.items():
        try:
            f = fit_family(name, x, seed)
            ll = float(np.sum(f.logpdf(x)))
            ks = float(stats.kstest(x, f.cdf).statistic)
        except Exception:  # noqa: BLE001 - a family may fail to converge on degenerate data
            continue
        rows.append(
            {
                "family": name,
                "k": k,
                "loglik": ll,
                "aic": 2 * k - 2 * ll,
                "bic": k * np.log(len(x)) - 2 * ll,
                "ks": ks,
            }
        )
    df = pd.DataFrame(rows).sort_values("aic").reset_index(drop=True)
    df["d_aic"] = df["aic"] - df["aic"].min()
    return df


def cv_families(groups: dict[str, np.ndarray], max_samples: int = 20000, seed: int = 0) -> pd.DataFrame:
    """Leave-one-group-out held-out mean log-likelihood per family (groups = batches or runs).

    Each group is centred on its own median first so that this ranks *shape* families, not bias.
    Higher is better. Out-of-sample, so it does not reward extra parameters the way AIC can.
    """
    rng = np.random.default_rng(seed)
    centred = {k: np.asarray(v, float) - np.median(v) for k, v in groups.items()}
    rows = []
    for name in FAMILIES:
        scores = []
        for held in centred:
            train = np.concatenate([v for k, v in centred.items() if k != held])
            if len(train) > max_samples:
                train = rng.choice(train, max_samples, replace=False)
            try:
                scores.append(float(np.mean(fit_family(name, train, seed).logpdf(centred[held]))))
            except Exception:  # noqa: BLE001
                scores.append(np.nan)
        rows.append(
            {"family": name, "mean_heldout_ll": np.nanmean(scores), "worst_group_ll": np.nanmin(scores)}
        )
    return pd.DataFrame(rows).sort_values("mean_heldout_ll", ascending=False).reset_index(drop=True)


# ----------------------------------------------------------------------------- k-group tests


@dataclass
class KruskalResult:
    h: float
    p: float
    epsilon_sq: float  # effect size: share of rank variance explained by the grouping


def kruskal_runs(values_by_group: dict[str, Sequence[float]]) -> KruskalResult:
    """Kruskal-Wallis on run-level summaries across several conditions, with an epsilon^2 effect size."""
    groups = [np.asarray(v, float) for v in values_by_group.values()]
    h, p = stats.kruskal(*groups)
    n = sum(len(g) for g in groups)
    return KruskalResult(float(h), float(p), float(h * (n + 1) / (n**2 - 1)))


def grouped_statistic(runs: Runs, fn: Callable[[np.ndarray], float]) -> np.ndarray:
    """Apply ``fn`` to each run; convenience for building run-level vectors for the tests above."""
    return np.array([fn(x) for x in runs])
