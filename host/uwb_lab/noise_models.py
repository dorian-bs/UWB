"""Baseline noise models, a leave-one-batch-out scoreboard and a simple generative sampler.

Every model describes the *error* ``err_m = raw_m - true_distance`` of one sample as
``loc(x) + scale(x) * noise`` with ``noise`` Gaussian or Student-t, where ``x`` are per-sample
covariates (condition columns, optionally radio diagnostics). Models are fitted on training batches
and scored on a held-out *batch*, because the claim we care about is generalisation to a new session,
not in-sample fit. To add a model, subclass ``NoiseModel`` (or just configure ``ParametricModel``)
and add a factory to the dict passed to ``scoreboard``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd
from scipy import stats


class NoiseModel:
    """Base class: subclasses implement ``_fit_loc_scale`` and ``predict``; the noise law is shared."""

    name = "model"

    def __init__(
        self, heavy_tail: bool = True, name: str | None = None, batch_col: str | None = None
    ) -> None:
        self.heavy_tail = heavy_tail
        self.batch_col = batch_col
        self.nu = np.inf
        self.t_scale = 1.0
        self.tau = 0.0  # sd of the unexplained per-batch offset (m); 0 unless ``batch_col`` is set
        if name:
            self.name = name

    # --- to implement
    def _fit_loc_scale(self, train: pd.DataFrame) -> None:
        raise NotImplementedError

    def _predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(loc, scale)`` arrays in metres from the fitted covariate model (no batch effect)."""
        raise NotImplementedError

    # --- shared
    def predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """``(loc, scale)`` for every row, with the batch-offset variance ``tau^2`` added to ``scale^2``.

        ``tau`` is what a *new* batch of an already-known condition still differs by (re-mounting, drift).
        It is estimated in-sample, so very flexible models underestimate it.
        """
        loc, scale = self._predict(df)
        return loc, np.sqrt(scale**2 + self.tau**2)

    def fit(self, train: pd.DataFrame, max_tail_samples: int = 50000, seed: int = 0) -> NoiseModel:
        self._fit_loc_scale(train)
        within = train
        if self.batch_col:
            # ``tau`` = sd of per-batch offsets; scale and tail are refitted on within-batch errors.
            loc, _ = self._predict(train)
            offsets = (
                pd.Series(train["err_m"].to_numpy() - loc).groupby(train[self.batch_col].to_numpy()).mean()
            )
            self.tau = float(offsets.std(ddof=1)) if len(offsets) > 2 else 0.0
            within = train.assign(
                err_m=train["err_m"].to_numpy() - train[self.batch_col].map(offsets).to_numpy()
            )
            self._fit_loc_scale(within)
        if self.heavy_tail:
            loc, scale = self._predict(within)
            z = (within["err_m"].to_numpy() - loc) / scale
            if len(z) > max_tail_samples:
                z = np.random.default_rng(seed).choice(z, max_tail_samples, replace=False)
            nu, _, self.t_scale = stats.t.fit(z, floc=0)
            self.nu = float(min(nu, 200.0))
        return self

    @property
    def law(self):
        return stats.t(self.nu, scale=self.t_scale) if self.heavy_tail else stats.norm()

    def logpdf(self, df: pd.DataFrame) -> np.ndarray:
        loc, scale = self.predict(df)
        z = (df["err_m"].to_numpy() - loc) / scale
        return self.law.logpdf(z) - np.log(scale)

    def interval(self, df: pd.DataFrame, level: float = 0.9) -> tuple[np.ndarray, np.ndarray]:
        loc, scale = self.predict(df)
        q = (1 - level) / 2
        law = self.law
        return loc + scale * law.ppf(q), loc + scale * law.ppf(1 - q)


class LookupModel(NoiseModel):
    """Mean/std per exact combination of ``by`` columns; unseen combinations fall back to the global fit.

    With ``by=[]`` this is the global baseline (a single Gaussian/Student-t for everything).
    """

    def __init__(
        self,
        by: Sequence[str] = (),
        heavy_tail: bool = True,
        name: str | None = None,
        batch_col: str | None = None,
    ) -> None:
        super().__init__(
            heavy_tail, name or ("global" if not by else "lookup[" + ",".join(by) + "]"), batch_col
        )
        self.by = list(by)

    def _fit_loc_scale(self, train: pd.DataFrame) -> None:
        self.g_loc, self.g_scale = float(train["err_m"].mean()), float(train["err_m"].std())
        self.table: dict = {}
        if self.by:
            agg = train.groupby(self.by, observed=True)["err_m"].agg(["mean", "std"])
            self.table = {k: (r["mean"], r["std"]) for k, r in agg.iterrows()}

    def _key(self, df: pd.DataFrame) -> list:
        cols = [df[c].to_numpy() for c in self.by]
        return list(zip(*cols, strict=True)) if len(cols) > 1 else list(cols[0])

    def _predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.by:
            return np.full(len(df), self.g_loc), np.full(len(df), self.g_scale)
        keys = self._key(df)
        cache = {}
        for k in set(keys):
            mean, std = self.table.get(k, (None, None))
            cache[k] = (mean, std) if std and std > 0 else (self.g_loc, self.g_scale)
        out = np.array([cache[k] for k in keys])
        return out[:, 0], out[:, 1]

    def share_known(self, df: pd.DataFrame) -> float:
        """Share of rows whose condition was seen in training (the rest use the global fallback)."""
        return 1.0 if not self.by else float(np.mean([k in self.table for k in self._key(df)]))


class ParametricModel(NoiseModel):
    """Bias and log-variance both linear in covariates; generalises to unseen condition combinations.

    ``categorical`` columns are one-hot encoded, ``numeric`` columns (e.g. ``distance_m``) are used
    as-is, ``diag`` columns (radio diagnostics, e.g. ``fp_gap_db``) are standardised and enter the
    *bias* (``diag_in_scale=True`` also lets them move the noise scale). ``loc = X b`` by ridge least
    squares, ``scale^2 = exp(X g) * c`` where ``g`` regresses log squared residuals and ``c`` is a
    smearing factor restoring calibration.
    """

    def __init__(
        self,
        categorical: Sequence[str] = (),
        numeric: Sequence[str] = ("distance_m",),
        diag: Sequence[str] = (),
        diag_in_scale: bool = False,
        heavy_tail: bool = True,
        ridge: float = 1e-6,
        name: str | None = None,
        batch_col: str | None = None,
    ) -> None:
        super().__init__(heavy_tail, name or "parametric", batch_col)
        self.categorical, self.numeric, self.diag = list(categorical), list(numeric), list(diag)
        self.diag_in_scale, self.ridge = diag_in_scale, ridge

    def _design(self, df: pd.DataFrame, with_diag: bool) -> np.ndarray:
        cols = [np.ones(len(df))]
        cols += [df[c].to_numpy(float) for c in self.numeric]
        for c in self.categorical:
            values = df[c].astype(str).to_numpy()
            cols += [(values == lvl).astype(float) for lvl in self.levels[c]]
        if with_diag:
            cols += [(df[c].to_numpy(float) - self.d_mean[c]) / self.d_std[c] for c in self.diag]
        return np.column_stack(cols)

    def _solve(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        a = x.T @ x + self.ridge * len(x) * np.eye(x.shape[1])
        return np.linalg.solve(a, x.T @ y)

    def _fit_loc_scale(self, train: pd.DataFrame) -> None:
        self.levels = {c: sorted(train[c].astype(str).unique()) for c in self.categorical}
        self.d_mean = {c: float(train[c].mean()) for c in self.diag}
        self.d_std = {c: float(train[c].std()) or 1.0 for c in self.diag}
        y = train["err_m"].to_numpy(float)
        xb = self._design(train, with_diag=True)
        self.beta = self._solve(xb, y)
        resid = y - xb @ self.beta
        xs = self._design(train, with_diag=self.diag_in_scale)
        self.gamma = self._solve(xs, np.log(np.maximum(resid**2, 1e-12)))
        self.smear = float(np.mean(resid**2 / np.exp(xs @ self.gamma)))

    def _predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        loc = self._design(df, with_diag=True) @ self.beta
        scale = np.sqrt(np.exp(self._design(df, with_diag=self.diag_in_scale) @ self.gamma) * self.smear)
        return loc, scale


# ----------------------------------------------------------------------------------- evaluation


def leave_one_batch_out(
    make_model: Callable[[], NoiseModel],
    samples: pd.DataFrame,
    require: Sequence[str] = (),
    min_samples: int = 200,
    group: str = "batch_id",
    held_out: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Fit on all other batches, score on the held-out one. One row per held-out batch.

    ``mean_ll`` is the mean log-density of the true errors (nats/sample, higher is better),
    ``cover90`` the share inside the model's 90% interval (target 0.90), ``bias_err_m`` the absolute
    error of the predicted mean offset. Rows missing any ``require`` column (diagnostics) are
    dropped for *every* model so that models are compared on identical data. ``held_out`` restricts
    which batches are scored (training always uses all the others), e.g. only batches that have a
    replicate, to measure "predict a repeat of a known condition".
    """
    data = samples.dropna(subset=list(require)) if require else samples
    labels = data[group].astype(str)
    rows = []
    for held in labels.unique():
        if held_out is not None and held not in held_out:
            continue
        mask = labels == held
        test = data[mask]
        if len(test) < min_samples:
            continue
        model = make_model().fit(data[~mask])
        lo, hi = model.interval(test)
        err = test["err_m"].to_numpy()
        loc, _ = model.predict(test)
        rows.append(
            {
                group: held,
                "n": len(test),
                "mean_ll": float(np.mean(model.logpdf(test))),
                "cover90": float(np.mean((err >= lo) & (err <= hi))),
                "bias_err_m": float(abs(err.mean() - loc.mean())),
            }
        )
    return pd.DataFrame(rows)


def scoreboard(
    factories: dict[str, Callable[[], NoiseModel]],
    samples: pd.DataFrame,
    require: Sequence[str] = (),
    held_out: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(summary, per_batch)`` for every model factory, best first by mean held-out log-lik."""
    frames = []
    for name, factory in factories.items():
        df = leave_one_batch_out(factory, samples, require, held_out=held_out)
        df.insert(0, "model", name)
        frames.append(df)
    per_batch = pd.concat(frames, ignore_index=True)
    summary = (
        per_batch.groupby("model")
        .agg(
            mean_ll=("mean_ll", "mean"),
            median_ll=("mean_ll", "median"),
            worst_ll=("mean_ll", "min"),
            cover90=("cover90", "mean"),
            bias_err_m=("bias_err_m", "mean"),
            n_batches=("n", "size"),
        )
        .sort_values("mean_ll", ascending=False)
    )
    return summary, per_batch


# --------------------------------------------------------------------------------- generative


def estimate_phi(runs: Sequence[np.ndarray]) -> float:
    """Median lag-1 autocorrelation over runs, used as the AR(1) coefficient of the sampler."""
    vals = []
    for x in runs:
        x = np.asarray(x, float) - np.mean(x)
        if len(x) > 8 and np.var(x) > 0:
            vals.append(float(np.sum(x[1:] * x[:-1]) / np.sum(x * x)))
    return float(np.median(vals))


def simulate_runs(
    model: NoiseModel,
    condition: dict,
    n_runs: int,
    n_samples: int,
    phi: float = 0.0,
    seed: int = 0,
    batch_effect: bool = False,
) -> list[np.ndarray]:
    """Sample synthetic error series for a condition (the columns the model uses, e.g. ``distance_m``).

    Marginal law = the model's (Gaussian or Student-t); temporal dependence = AR(1) Gaussian copula with
    coefficient ``phi``, so the marginal is exact while the lag-1 correlation is approximate.
    ``batch_effect=True`` draws one N(0, tau) offset shared by all simulated runs (they form one batch).
    Only for models without per-sample diagnostics (those would need simulated diagnostics too).
    """
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame([condition] * n_samples)
    frame["err_m"] = 0.0
    loc, scale = model._predict(frame)
    if batch_effect:
        loc = loc + rng.normal(0, model.tau)
    law = model.law
    out = []
    for _ in range(n_runs):
        e = rng.standard_normal(n_samples)
        g = np.empty(n_samples)
        g[0] = e[0]
        for i in range(1, n_samples):
            g[i] = phi * g[i - 1] + np.sqrt(1 - phi**2) * e[i]
        out.append(loc + scale * law.ppf(stats.norm.cdf(g)))
    return out
