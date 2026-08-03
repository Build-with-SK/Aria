"""
src/v5/modules/quant.py
=======================
Quant & Statistical family — Statistical Arbitrage, Cointegration, PCA,
Clustering, Hidden Markov (Gaussian mixture + empirical transitions), Kalman
Filter, Bayesian, Time Series.

Two of these produce intervals that are genuinely *estimated* rather than
assumed — the Kalman filter's posterior on the trend slope, and the Bayesian
module's Beta credible interval. Those are the only places in the platform where
the interval falls out of the model itself. Everything else is an approximation
and says so in its weaknesses.

statsmodels and hmmlearn are not installed in this environment, so the ADF test
and the Markov regime model are implemented directly on numpy/sklearn. Both are
labelled as approximations where they are.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.v5 import marketdata as md
from src.v5.contract import Evidence, ModuleReport, insufficient, wilson_interval
from src.v5.modules._util import (ann_vol, clamp, conditional_hit_rate,
                                  effective_interval, hit_rate_probability,
                                  overlap_weakness, pct, regime_weakness, rsi, sma)
from src.v5.registry import module

HORIZON = 21
MIN_BARS = 260


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _aligned(a: pd.Series, b: pd.Series) -> pd.DataFrame:
    return pd.concat([a.rename("y"), b.rename("x")], axis=1, sort=True).dropna()


def _best_partner(ticker: str) -> tuple[str, pd.Series] | tuple[None, None]:
    """The most correlated available series to pair against: a peer if one
    loads, otherwise the benchmark."""
    y = md.closes(ticker, period="3y")
    if y is None:
        return (None, None)
    best, best_r, best_s = None, -2.0, None
    candidates = [p.get("symbol") or p.get("yahoo") for p in (md.peers(ticker, 4) or [])]
    candidates.append(md.BENCH)
    for sym in [c for c in candidates if c and c != ticker]:
        s = md.closes(sym, period="3y")
        if s is None:
            continue
        df = _aligned(y, s)
        if len(df) < 200:
            continue
        r = float(df["y"].pct_change().corr(df["x"].pct_change()))
        if not math.isnan(r) and r > best_r:
            best, best_r, best_s = sym, r, s
    return (best, best_s) if best else (None, None)


# ── Statistical arbitrage ───────────────────────────────────────────────────

@module("statistical_arbitrage", "quant",
        "Spread z-score against its most correlated partner, with its own reversion base rate.",
        horizon_days=10)
def statistical_arbitrage(ticker: str) -> ModuleReport:
    h = 10
    partner, ps = _best_partner(ticker)
    y = md.closes(ticker, period="3y")
    if partner is None or y is None:
        return insufficient("statistical_arbitrage", "quant", ticker,
                            "no correlated partner series could be loaded")
    df = _aligned(y, ps)
    if len(df) < MIN_BARS:
        return insufficient("statistical_arbitrage", "quant", ticker,
                            f"only {len(df)} overlapping sessions with {partner}")

    ly, lx = np.log(df["y"].to_numpy()), np.log(df["x"].to_numpy())
    beta, alpha = np.polyfit(lx, ly, 1)
    spread = pd.Series(ly - (alpha + beta * lx), index=df.index)
    mu, sd = float(spread.rolling(120).mean().iloc[-1]), float(spread.rolling(120).std().iloc[-1])
    if not sd or math.isnan(sd) or sd == 0:
        return insufficient("statistical_arbitrage", "quant", ticker,
                            "spread has no usable dispersion")
    z = (float(spread.iloc[-1]) - mu) / sd
    zs = (spread - spread.rolling(120).mean()) / spread.rolling(120).std()

    if z <= -1.0:
        mask, state = zs <= -1.0, f"cheap versus {partner}"
    elif z >= 1.0:
        mask, state = zs >= 1.0, f"rich versus {partner}"
    else:
        mask, state = zs.abs() < 1.0, f"fairly priced versus {partner}"

    hr = conditional_hit_rate(df["y"], mask.fillna(False), h)
    est = hit_rate_probability(hr[0], hr[1])
    if est is None:
        return insufficient("statistical_arbitrage", "quant", ticker,
                            f"only {hr[1]} observations at this spread state")
    p, ci = est
    corr = float(df["y"].pct_change().corr(df["x"].pct_change()))
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = f"Yahoo Finance ({ticker} vs {partner} daily closes, 3y)"

    return ModuleReport.from_probability(
        "statistical_arbitrage", "quant", ticker, p, ci,
        thesis=(f"Against {partner} (daily return correlation {corr:.2f}, hedge ratio {beta:.2f}), "
                f"{ticker} is {state} at {z:+.2f} spread standard deviations. From that state the "
                f"next {h} sessions were positive {p:.0%} of the time."),
        evidence=[
            Evidence(f"Log-spread z-score {z:+.2f} versus {partner}", round(z, 3), src, lean),
            Evidence(f"Hedge ratio (OLS beta of log price) {beta:.3f}", round(float(beta), 4), src, "neutral"),
            Evidence(f"Daily return correlation with {partner}: {corr:.2f}", round(corr, 3), src, "neutral"),
            Evidence(f"Base rate at this state: {hr[0]}/{hr[1]} positive ({p:.0%})",
                     round(p, 4), src, lean),
        ],
        weaknesses=[overlap_weakness(h),
                    "The pair is selected by correlation over the same window used to test it — that "
                    "is a mild in-sample selection bias in favour of the module.",
                    "A spread can be wide because the relationship broke, not because it is stretched; "
                    "nothing here detects that except the cointegration module.",
                    "Outright long exposure is not a hedged pair trade — this signal is strictly about "
                    "relative value and is being read directionally."],
        horizon_days=h, n_obs=hr[1])


# ── Cointegration ───────────────────────────────────────────────────────────

def _adf_stat(series: np.ndarray, lags: int = 1) -> float | None:
    """Augmented Dickey-Fuller t-statistic, computed directly (statsmodels is
    not installed here). Regression: dy_t = a + g*y_{t-1} + sum(b_i*dy_{t-i})."""
    y = np.asarray(series, dtype=float)
    dy = np.diff(y)
    n = len(dy) - lags
    if n < 60:
        return None
    Y = dy[lags:]
    cols = [np.ones(n), y[lags:-1]]
    for i in range(1, lags + 1):
        cols.append(dy[lags - i:-i] if i else dy)
    X = np.column_stack(cols[:2 + lags])
    try:
        beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
        resid = Y - X @ beta
        dof = n - X.shape[1]
        s2 = float(resid @ resid) / dof
        xtx_inv = np.linalg.inv(X.T @ X)
        se = math.sqrt(s2 * xtx_inv[1, 1])
        return float(beta[1] / se) if se > 0 else None
    except Exception:
        return None


# MacKinnon asymptotic critical values, constant-no-trend case.
ADF_CRIT = {0.01: -3.43, 0.05: -2.86, 0.10: -2.57}


@module("cointegration", "quant",
        "Engle-Granger test of whether the relationship being traded actually holds.",
        horizon_days=HORIZON)
def cointegration(ticker: str) -> ModuleReport:
    partner, ps = _best_partner(ticker)
    y = md.closes(ticker, period="3y")
    if partner is None or y is None:
        return insufficient("cointegration", "quant", ticker, "no partner series available")
    df = _aligned(y, ps)
    if len(df) < MIN_BARS:
        return insufficient("cointegration", "quant", ticker,
                            f"only {len(df)} overlapping sessions with {partner}")

    ly, lx = np.log(df["y"].to_numpy()), np.log(df["x"].to_numpy())
    beta, alpha = np.polyfit(lx, ly, 1)
    resid = ly - (alpha + beta * lx)
    stat = _adf_stat(resid)
    if stat is None:
        return insufficient("cointegration", "quant", ticker,
                            "ADF regression could not be estimated on the residual")

    cointegrated = stat < ADF_CRIT[0.05]
    level = ("1%" if stat < ADF_CRIT[0.01] else "5%" if stat < ADF_CRIT[0.05]
             else "10%" if stat < ADF_CRIT[0.10] else "not rejected")

    # Half-life of mean reversion from an AR(1) on the residual.
    r = pd.Series(resid)
    lag = r.shift(1).dropna()
    dr = r.diff().dropna()
    k = float(np.polyfit(lag.to_numpy(), dr.loc[lag.index].to_numpy(), 1)[0])
    half_life = float(-math.log(2) / k) if k < 0 else float("inf")

    z = float((resid[-1] - resid[-120:].mean()) / (resid[-120:].std() or 1))
    # Only when the relationship actually holds does the spread carry direction.
    p = clamp(0.5 - 0.12 * z, 0.0, 1.0) if cointegrated else 0.5
    ci = effective_interval(p, 2 if cointegrated else 1, floor=0.24 if cointegrated else 0.60)
    src = f"Yahoo Finance ({ticker} vs {partner}, 3y log closes)"

    return ModuleReport.from_probability(
        "cointegration", "quant", ticker, p, ci,
        thesis=(f"ADF t-statistic on the {ticker}~{partner} residual is {stat:.2f} "
                f"({'stationary at the ' + level if cointegrated else 'unit root not rejected'}). "
                + (f"Half-life of reversion {half_life:.0f} sessions; the spread sits {z:+.2f}σ from "
                   f"its mean, so reversion points {'down' if z > 0 else 'up'}."
                   if cointegrated else
                   "Without a stationary residual, any spread signal on this pair is noise, so this "
                   "module returns a deliberately uninformative 50/50 with a very wide interval.")),
        evidence=[
            Evidence(f"ADF statistic {stat:.2f} vs 5% critical value {ADF_CRIT[0.05]}",
                     round(stat, 3), src, "neutral"),
            Evidence(f"Cointegration with {partner}: {'yes at ' + level if cointegrated else 'no'}",
                     None, src, "neutral"),
            Evidence(f"Half-life of mean reversion {half_life:.0f} sessions"
                     if math.isfinite(half_life) else "Residual is not mean-reverting (no half-life)",
                     round(half_life, 1) if math.isfinite(half_life) else None, src, "neutral"),
            Evidence(f"Residual currently {z:+.2f}σ from its 120-day mean", round(z, 3), src,
                     "bull" if (cointegrated and z < 0) else "bear" if (cointegrated and z > 0) else "neutral"),
        ],
        weaknesses=["Critical values are MacKinnon asymptotics hard-coded here, not computed from the "
                    "sample; borderline statistics near -2.86 should be treated as inconclusive.",
                    "The partner is chosen by correlation on the same sample the test runs on, which "
                    "inflates the chance of finding cointegration by luck.",
                    "Cointegration is notoriously unstable out of sample — it breaks precisely when "
                    "one of the two names re-rates.",
                    "Two series, one test: this is the thinnest evidence base of any module here."],
        horizon_days=HORIZON, n_obs=len(df))


# ── PCA ─────────────────────────────────────────────────────────────────────

@module("pca", "quant",
        "Decomposition into a common market factor and the name's idiosyncratic residual.",
        horizon_days=HORIZON)
def pca(ticker: str) -> ModuleReport:
    from sklearn.decomposition import PCA as _PCA

    universe = {ticker: md.returns(ticker, period="2y")}
    for label, sym in md.CROSS_ASSET.items():
        if sym == ticker:
            continue
        r = md.returns(sym, period="2y")
        if r is not None:
            universe[sym] = r
    frame = pd.DataFrame({k: v for k, v in universe.items() if v is not None}).dropna()
    if ticker not in frame.columns or frame.shape[1] < 4 or len(frame) < 200:
        return insufficient("pca", "quant", ticker,
                            f"only {frame.shape[1]} series / {len(frame)} rows aligned — too thin for PCA")

    X = frame.to_numpy()
    model = _PCA(n_components=min(3, X.shape[1]))
    scores = model.fit_transform(X - X.mean(axis=0))
    evr = model.explained_variance_ratio_
    idx = list(frame.columns).index(ticker)
    loading = float(model.components_[0][idx])

    # Idiosyncratic return = what PC1 does not explain, cumulated over a month.
    pc1_contrib = np.outer(scores[:, 0], model.components_[0])[:, idx]
    resid = frame[ticker].to_numpy() - X.mean(axis=0)[idx] - pc1_contrib
    resid_s = pd.Series(resid, index=frame.index)
    recent = float(resid_s.tail(21).sum())
    sd = float(resid_s.std() * math.sqrt(21))
    z = recent / sd if sd else 0.0

    # Idiosyncratic strength is read as continuation, tempered — the direction of
    # this reading is a prior, so the interval stays wide.
    p = clamp(0.5 + 0.10 * z, 0.0, 1.0)
    ci = effective_interval(p, 3, floor=0.28)
    src = "Yahoo Finance (2y daily returns, cross-asset panel)"

    return ModuleReport.from_probability(
        "pca", "quant", ticker, p, ci,
        thesis=(f"PC1 explains {evr[0]:.0%} of variance across the {frame.shape[1]}-series panel and "
                f"{ticker} loads {loading:+.2f} on it. Over the last month the name generated "
                f"{pct(recent, 2)} of idiosyncratic return, {z:+.2f}σ."),
        evidence=[
            Evidence(f"PC1 explains {evr[0]:.0%} of panel variance (PC2 {evr[1]:.0%})",
                     round(float(evr[0]), 4), src, "neutral"),
            Evidence(f"{ticker} loading on PC1: {loading:+.3f}", round(loading, 4), src, "neutral"),
            Evidence(f"21-day idiosyncratic return {pct(recent, 2)} ({z:+.2f}σ)",
                     round(recent, 4), src, "bull" if z > 0 else "bear"),
            Evidence(f"Panel: {frame.shape[1]} series, {len(frame)} aligned sessions",
                     frame.shape[1], src, "neutral"),
        ],
        weaknesses=["PC1 is interpreted as 'the market' — that is an interpretation, not an "
                    "identification; in a commodity shock PC1 is something else entirely.",
                    "The panel is cross-asset proxies, not the name's true peer group, so the residual "
                    "still contains sector risk.",
                    "Reading positive idiosyncratic return as continuation is a stated prior with no "
                    "outcome calibration behind it, which is why the interval here is wide.",
                    "PCA loadings are unstable across sample windows and unsigned by construction."],
        horizon_days=HORIZON, n_obs=len(frame))


# ── Clustering ──────────────────────────────────────────────────────────────

@module("clustering", "quant",
        "K-means over historical market states; forward base rate of the current cluster.",
        horizon_days=HORIZON)
def clustering(ticker: str) -> ModuleReport:
    from sklearn.cluster import KMeans

    c = md.closes(ticker, period="5y")
    if c is None or len(c) < 400:
        return insufficient("clustering", "quant", ticker,
                            "fewer than 400 daily closes — clusters would be unreliable")
    df = pd.DataFrame({
        "ret5": c.pct_change(5),
        "ret21": c.pct_change(21),
        "vol21": c.pct_change().rolling(21).std(),
        "rsi": rsi(c, 14) / 100.0,
        "dist200": c / sma(c, 200) - 1.0,
    }).dropna()
    if len(df) < 300:
        return insufficient("clustering", "quant", ticker, "feature matrix too short after alignment")

    Z = (df - df.mean()) / df.std().replace(0, 1)
    k = 5
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(Z.to_numpy())
    labels = pd.Series(km.labels_, index=df.index)
    current = int(labels.iloc[-1])
    mask = labels == current

    hr = conditional_hit_rate(c, mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1], min_n=25)
    if est is None:
        return insufficient("clustering", "quant", ticker,
                            f"current cluster has only {hr[1]} historical forward observations")
    p, ci = est
    centre = df[mask].mean()
    share = float(mask.mean())
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = md.source_label(ticker)

    return ModuleReport.from_probability(
        "clustering", "quant", ticker, p, ci,
        thesis=(f"Today's state falls in cluster {current} of {k} ({share:.0%} of the last five years): "
                f"21-day return {pct(float(centre['ret21']))}, realised vol "
                f"{pct(float(centre['vol21']) * math.sqrt(252))} annualised, RSI "
                f"{float(centre['rsi']) * 100:.0f}, {pct(float(centre['dist200']))} from the 200dma. "
                f"That cluster resolved up {p:.0%} of the time."),
        evidence=[
            Evidence(f"Cluster {current}/{k} covers {share:.0%} of the 5-year sample",
                     round(share, 3), src, "neutral"),
            Evidence(f"Cluster centroid: 21d return {pct(float(centre['ret21']))}, RSI "
                     f"{float(centre['rsi']) * 100:.0f}, {pct(float(centre['dist200']))} vs 200dma",
                     round(float(centre["ret21"]), 4), src, lean),
            Evidence(f"Forward {HORIZON}-day base rate in this cluster: {hr[0]}/{hr[1]} positive "
                     f"({p:.0%})", round(p, 4), src, lean),
            Evidence(f"Mean forward return from this cluster {pct(hr[2], 2)}", round(hr[2], 4), src,
                     "bull" if hr[2] > 0 else "bear"),
        ],
        weaknesses=[overlap_weakness(HORIZON), regime_weakness(),
                    "k=5 and the feature set are chosen, not optimised; a different k gives a "
                    "different answer and there is no cross-validation here.",
                    "Clusters are fitted on the whole sample including the present, which leaks a "
                    "small amount of future information into the cluster definition."],
        horizon_days=HORIZON, n_obs=hr[1])


# ── Hidden Markov regime ────────────────────────────────────────────────────

@module("hidden_markov", "quant",
        "Two-state Gaussian regime model with empirically estimated transitions.",
        horizon_days=HORIZON)
def hidden_markov(ticker: str) -> ModuleReport:
    from sklearn.mixture import GaussianMixture

    r = md.returns(ticker, period="5y")
    c = md.closes(ticker, period="5y")
    if r is None or c is None or len(r) < 400:
        return insufficient("hidden_markov", "quant", ticker,
                            "fewer than 400 return observations")

    X = r.to_numpy().reshape(-1, 1)
    gm = GaussianMixture(n_components=2, covariance_type="full", random_state=42,
                         n_init=5).fit(X)
    states = pd.Series(gm.predict(X), index=r.index)
    means = [float(m[0]) for m in gm.means_]
    vols = [float(math.sqrt(cv[0][0]) * math.sqrt(252)) for cv in gm.covariances_]
    calm = int(np.argmin(vols))
    cur = int(states.iloc[-1])
    label = "calm/low-volatility" if cur == calm else "stressed/high-volatility"

    # Empirical transition matrix from the hard state sequence.
    trans = np.zeros((2, 2))
    s = states.to_numpy()
    for a, b in zip(s[:-1], s[1:]):
        trans[a, b] += 1
    persistence = float(trans[cur, cur] / max(1, trans[cur].sum()))

    mask = states == cur
    hr = conditional_hit_rate(c, mask, HORIZON)
    est = hit_rate_probability(hr[0], hr[1], min_n=25)
    if est is None:
        return insufficient("hidden_markov", "quant", ticker,
                            f"only {hr[1]} forward observations in the current regime")
    p, ci = est
    lean = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    src = md.source_label(ticker)
    post = float(gm.predict_proba(X[-1].reshape(1, -1))[0][cur])

    return ModuleReport.from_probability(
        "hidden_markov", "quant", ticker, p, ci,
        thesis=(f"{ticker} is in the {label} regime (posterior {post:.0%}, persistence "
                f"{persistence:.0%} per day). That regime carries {vols[cur]:.0%} annualised "
                f"volatility and a drift of {means[cur] * 252 * 100:+.1f}% annualised; forward "
                f"{HORIZON}-day returns from it were positive {p:.0%} of the time."),
        evidence=[
            Evidence(f"Current regime: {label} (posterior probability {post:.0%})",
                     round(post, 3), src, "neutral"),
            Evidence(f"Regime volatility {vols[cur]:.0%} annualised vs "
                     f"{vols[1 - cur]:.0%} in the other state", round(vols[cur], 4), src,
                     "bear" if cur != calm else "bull"),
            Evidence(f"Regime drift {means[cur] * 252 * 100:+.1f}% annualised",
                     round(means[cur] * 252, 4), src, "bull" if means[cur] > 0 else "bear"),
            Evidence(f"Daily persistence of this regime {persistence:.0%}", round(persistence, 3),
                     src, "neutral"),
            Evidence(f"Forward base rate in regime: {hr[0]}/{hr[1]} positive ({p:.0%})",
                     round(p, 4), src, lean),
        ],
        weaknesses=["hmmlearn is not installed, so this is a two-component Gaussian mixture with "
                    "transitions counted from hard state assignments — not a Baum-Welch-fitted HMM. "
                    "The state sequence is therefore noisier than a true Viterbi path.",
                    "Two states is an assumption; real markets show at least three (calm bull, calm "
                    "grind, stressed).",
                    overlap_weakness(HORIZON),
                    "The model is fitted on the full sample, so the regime definition sees the present."],
        horizon_days=HORIZON, n_obs=hr[1])


# ── Kalman filter ───────────────────────────────────────────────────────────

@module("kalman", "quant",
        "Local linear trend filter — trend slope with a genuine posterior interval.",
        horizon_days=HORIZON)
def kalman(ticker: str) -> ModuleReport:
    c = md.closes(ticker, period="2y")
    if c is None or len(c) < 120:
        return insufficient("kalman", "quant", ticker, "fewer than 120 daily closes")

    y = np.log(c.to_numpy())
    n = len(y)
    obs_var = float(np.var(np.diff(y))) or 1e-6
    q_level, q_slope = obs_var * 0.05, obs_var * 0.001      # stated process noise
    R = obs_var

    x = np.array([y[0], 0.0])
    P = np.eye(2) * obs_var * 10
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    Q = np.array([[q_level, 0.0], [0.0, q_slope]])
    H = np.array([[1.0, 0.0]])

    for t in range(1, n):
        x = F @ x
        P = F @ P @ F.T + Q
        resid = y[t] - float((H @ x)[0])
        S = float((H @ P @ H.T)[0, 0]) + R
        K = (P @ H.T / S).ravel()
        x = x + K * resid
        P = (np.eye(2) - np.outer(K, H)) @ P

    slope = float(x[1])
    slope_sd = float(math.sqrt(max(P[1, 1], 1e-12)))
    mu_h = slope * HORIZON
    sigma_h = math.sqrt(HORIZON * obs_var + (HORIZON * slope_sd) ** 2)
    p = _norm_cdf(mu_h / sigma_h) if sigma_h > 0 else 0.5

    # The interval comes straight from the filter's posterior on the slope.
    lo_slope, hi_slope = slope - 1.96 * slope_sd, slope + 1.96 * slope_sd
    ci = (round(_norm_cdf(lo_slope * HORIZON / sigma_h), 4),
          round(_norm_cdf(hi_slope * HORIZON / sigma_h), 4))
    src = md.source_label(ticker)
    lean = "bull" if slope > 0 else "bear"

    return ModuleReport.from_probability(
        "kalman", "quant", ticker, p, ci,
        thesis=(f"The filtered trend slope is {slope * 252 * 100:+.1f}% annualised "
                f"(±{slope_sd * 252 * 100:.1f}pp at one standard deviation). Projected over "
                f"{HORIZON} sessions against the filter's own noise estimate, P(positive) = {p:.0%}. "
                f"This interval is estimated by the model, not assumed."),
        evidence=[
            Evidence(f"Filtered slope {slope * 252 * 100:+.2f}% annualised", round(slope, 6), src, lean),
            Evidence(f"Posterior standard deviation on the slope "
                     f"{slope_sd * 252 * 100:.2f}pp annualised", round(slope_sd, 6), src, "neutral"),
            Evidence(f"Observation noise (daily log-return variance) {obs_var:.2e}",
                     round(obs_var, 8), src, "neutral"),
            Evidence(f"{HORIZON}-session projected move {pct(math.exp(mu_h) - 1, 2)} "
                     f"± {pct(sigma_h, 2)}", round(mu_h, 5), src, lean),
        ],
        weaknesses=["Process-noise ratios (5% and 0.1% of observation variance) are chosen, not "
                    "estimated by maximum likelihood — a different choice changes how fast the trend "
                    "adapts and therefore the answer.",
                    "A local linear trend assumes Gaussian innovations; real returns are fat-tailed, so "
                    "the tails of this projection are too thin.",
                    "The filter extrapolates the current slope forward, which is exactly what fails at "
                    "turning points."],
        horizon_days=HORIZON, n_obs=n)


# ── Bayesian ────────────────────────────────────────────────────────────────

@module("bayesian", "quant",
        "Beta-binomial posterior: market-wide prior updated by this instrument's evidence.",
        horizon_days=HORIZON)
def bayesian(ticker: str) -> ModuleReport:
    from scipy import stats

    c = md.closes(ticker, period="2y")
    b = md.closes(md.BENCH, period="5y")
    if c is None or len(c) < 300:
        return insufficient("bayesian", "quant", ticker, "fewer than 300 daily closes")

    # Prior: the benchmark's own long-run frequency of positive 21-day windows,
    # given a deliberately modest weight of 30 pseudo-observations.
    prior_w = 30.0
    if b is not None and len(b) > 400:
        fwd_b = (b.shift(-HORIZON) / b - 1).dropna()
        prior_p = float((fwd_b > 0).mean())
        prior_src = f"{md.BENCH} 5y base rate"
    else:
        prior_p, prior_src = 0.5, "uninformative 50/50 (benchmark unavailable)"
    a0, b0 = prior_p * prior_w, (1 - prior_p) * prior_w

    fwd = (c.shift(-HORIZON) / c - 1).dropna()
    k, n = int((fwd > 0).sum()), int(len(fwd))
    if n < 60:
        return insufficient("bayesian", "quant", ticker,
                            f"only {n} forward windows available for the likelihood")

    # Overlapping windows are not independent: discount the evidence count by the
    # horizon so the posterior is not falsely sharp.
    scale = 1.0 / HORIZON
    a, bb = a0 + k * scale, b0 + (n - k) * scale
    post = a / (a + bb)
    lo, hi = stats.beta.ppf(0.025, a, bb), stats.beta.ppf(0.975, a, bb)
    src = md.source_label(ticker)
    lean = "bull" if post > 0.55 else "bear" if post < 0.45 else "neutral"

    return ModuleReport.from_probability(
        "bayesian", "quant", ticker, post, (float(lo), float(hi)),
        thesis=(f"Prior: {prior_p:.0%} ({prior_src}, weight {prior_w:.0f}). Evidence: {ticker} closed "
                f"a {HORIZON}-day window higher {k}/{n} times ({k / n:.0%}), discounted to "
                f"{n * scale:.0f} independent observations for overlap. Posterior "
                f"{post:.0%} [{lo:.0%}, {hi:.0%}]."),
        evidence=[
            Evidence(f"Prior {prior_p:.0%} from {prior_src}", round(prior_p, 4),
                     f"Yahoo Finance ({md.BENCH} 5y)", "neutral"),
            Evidence(f"Instrument evidence {k}/{n} positive {HORIZON}-day windows ({k / n:.0%})",
                     round(k / n, 4), src, lean),
            Evidence(f"Overlap discount: {n} raw windows counted as {n * scale:.0f} independent",
                     round(n * scale, 1), src, "neutral"),
            Evidence(f"Posterior Beta({a:.1f}, {bb:.1f}) → {post:.1%} "
                     f"[{lo:.1%}, {hi:.1%}]", round(post, 4), src, lean),
        ],
        weaknesses=["This is an unconditional base rate — it deliberately ignores where the instrument "
                    "is right now, which is the whole job of every other module.",
                    "The overlap discount (dividing by the horizon) is a conservative rule of thumb, "
                    "not a formally derived effective sample size.",
                    "A two-year likelihood window carries a regime assumption: it weights the recent "
                    "regime fully and everything before it not at all.",
                    "The prior is US-equity-shaped; for crypto or FX it is close to meaningless and "
                    "only its low weight limits the damage."],
        horizon_days=HORIZON, n_obs=n)


# ── Time series ─────────────────────────────────────────────────────────────

@module("time_series", "quant",
        "AR(5) forecast of the cumulative horizon return with a prediction interval.",
        horizon_days=HORIZON)
def time_series(ticker: str) -> ModuleReport:
    r = md.returns(ticker, period="3y")
    if r is None or len(r) < 300:
        return insufficient("time_series", "quant", ticker, "fewer than 300 return observations")

    lags = 5
    y = r.to_numpy()
    X = np.column_stack([np.ones(len(y) - lags)] +
                        [y[lags - i - 1: -i - 1] for i in range(lags)])
    Y = y[lags:]
    try:
        beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    except Exception:
        return insufficient("time_series", "quant", ticker, "AR regression failed to converge")
    resid = Y - X @ beta
    sigma = float(np.std(resid))
    r2 = 1.0 - float(np.var(resid) / np.var(Y)) if np.var(Y) else 0.0

    # Iterate the AR forward over the horizon.
    hist = list(y[-lags:])
    path = []
    for _ in range(HORIZON):
        nxt = float(beta[0] + sum(beta[i + 1] * hist[-1 - i] for i in range(lags)))
        path.append(nxt)
        hist.append(nxt)
    mu = float(sum(path))
    sigma_h = sigma * math.sqrt(HORIZON)
    p = _norm_cdf(mu / sigma_h) if sigma_h > 0 else 0.5

    # Interval from the standard error of the forecast mean.
    se_mu = sigma_h / math.sqrt(max(1, len(Y)))
    ci = (round(_norm_cdf((mu - 1.96 * se_mu * math.sqrt(HORIZON)) / sigma_h), 4),
          round(_norm_cdf((mu + 1.96 * se_mu * math.sqrt(HORIZON)) / sigma_h), 4))
    if ci[1] - ci[0] < 0.20:                 # an AR(5) on daily returns explains almost nothing
        mid = (ci[0] + ci[1]) / 2
        ci = (max(0.0, mid - 0.10), min(1.0, mid + 0.10))
    src = md.source_label(ticker)
    lean = "bull" if mu > 0 else "bear"

    return ModuleReport.from_probability(
        "time_series", "quant", ticker, p, ci,
        thesis=(f"An AR(5) on daily returns explains {r2:.1%} of variance and projects "
                f"{pct(mu, 2)} cumulative over {HORIZON} sessions against a {pct(sigma_h, 2)} "
                f"one-sigma band — P(positive) {p:.0%}. The tiny R² is the point: daily returns are "
                f"close to unforecastable and this module should rarely move the ensemble."),
        evidence=[
            Evidence(f"AR(5) in-sample R² {r2:.2%}", round(r2, 5), src, "neutral"),
            Evidence(f"Projected {HORIZON}-session cumulative return {pct(mu, 2)}", round(mu, 5),
                     src, lean),
            Evidence(f"Residual volatility {pct(sigma, 2)} daily, {pct(sigma_h, 2)} over the horizon",
                     round(sigma, 5), src, "neutral"),
            Evidence(f"Strongest lag coefficient {max(beta[1:], key=abs):+.4f}",
                     round(float(max(beta[1:], key=abs)), 5), src, "neutral"),
        ],
        weaknesses=["In-sample R² with no walk-forward validation — the true out-of-sample R² on daily "
                    "equity returns is normally indistinguishable from zero.",
                    "Iterating an AR forward compounds its own errors; by day 21 the point forecast is "
                    "essentially the unconditional mean.",
                    "Homoskedastic Gaussian residuals are assumed; returns are neither.",
                    "The interval is floored at 20 points wide because the honest uncertainty here is "
                    "larger than the regression's standard error suggests."],
        horizon_days=HORIZON, n_obs=len(Y))
