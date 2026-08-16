"""
src/evolution/statistics.py
===========================
THE PART THAT MAKES THIS HONEST.

Breed four thousand strategies, keep the ones that survive an unseen-data
exam, and you have not found skill — you have found the strategies that got
lucky twice. With four thousand trials, the best in-sample Sharpe is roughly
3 even when EVERY strategy is pure noise, and a decent fraction of those will
clear a single out-of-sample test by chance. That is not a flaw in the search.
It is arithmetic, and no amount of extra data fixes it, because the bar itself
has to move when the number of trials goes up.

So this module supplies the moving bar:

  expected_max_sharpe   What the LUCKIEST of N worthless strategies scores.
                        The null hypothesis for a search is not "Sharpe 0",
                        it is this.

  deflated_sharpe       Bailey & López de Prado (2014). The probability that
                        a strategy's true Sharpe exceeds zero, after charging
                        it for the number of trials, the spread of trial
                        results, and its own skew and fat tails — because a
                        Sharpe earned by selling tail risk is not the same
                        Sharpe.

  purged_walk_forward   Splits with a gap between train and test. Adjacent
                        days share information; testing on the day after
                        training leaks, and leaked results are the most
                        convincing wrong answers a backtest can produce.

References: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014);
López de Prado, "Advances in Financial Machine Learning" (2018), ch. 7.
"""
from __future__ import annotations

import math

EULER_MASCHERONI = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, ~1e-9 accurate).

    Written out rather than imported so the honesty of this module never
    depends on scipy being installed.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")

    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def expected_max_sharpe(trial_sharpes: list[float], n_trials: int | None = None) -> float:
    """The Sharpe the luckiest of N worthless strategies would post.

    This is the bar a searched strategy must beat to have said anything. It
    rises with both the number of trials and how spread out their results
    were: a search over a wild space gets lucky more easily than one over a
    narrow space, and deserves more scepticism for it.
    """
    trials = [s for s in trial_sharpes if s is not None and math.isfinite(s)]
    n = int(n_trials or len(trials))
    if n < 2 or len(trials) < 2:
        return 0.0

    mean = sum(trials) / len(trials)
    variance = sum((s - mean) ** 2 for s in trials) / (len(trials) - 1)
    sigma = math.sqrt(max(variance, 0.0))
    if sigma <= 0:
        return 0.0

    # E[max of n draws] ≈ σ·[(1-γ)·Φ⁻¹(1 - 1/n) + γ·Φ⁻¹(1 - 1/(n·e))]
    term = ((1 - EULER_MASCHERONI) * _norm_ppf(1 - 1.0 / n)
            + EULER_MASCHERONI * _norm_ppf(1 - 1.0 / (n * math.e)))
    return sigma * term


def deflated_sharpe(observed_sharpe: float, trial_sharpes: list[float],
                    n_observations: int, skew: float = 0.0,
                    kurtosis: float = 3.0,
                    n_trials: int | None = None) -> dict:
    """P(true Sharpe > 0) for a strategy that was CHOSEN by a search.

    Returns the probability alongside the bar it had to clear, because the
    bar is the interesting number: "Sharpe 2.1 against a luck threshold of
    1.9" tells you the whole story and "Sharpe 2.1" tells you nothing.

    All Sharpes here are annualised; n_observations is the number of return
    periods behind the estimate. Negative skew and fat tails REDUCE the
    probability — a Sharpe earned by picking up pennies is worth less than
    the same number earned symmetrically.
    """
    threshold = expected_max_sharpe(trial_sharpes, n_trials)
    n = int(n_observations)
    if n < 3 or observed_sharpe is None or not math.isfinite(observed_sharpe):
        return {"probability": None, "threshold": round(threshold, 4),
                "observed": observed_sharpe, "n_trials": n_trials or len(trial_sharpes),
                "verdict": "insufficient data"}

    # Non-normality correction: the variance of a Sharpe estimate grows with
    # negative skew and with excess kurtosis.
    denominator = 1 - skew * observed_sharpe + ((kurtosis - 1) / 4) * observed_sharpe ** 2
    if denominator <= 0:
        denominator = 1e-9

    z = ((observed_sharpe - threshold) * math.sqrt(n - 1)) / math.sqrt(denominator)
    probability = _norm_cdf(z)

    return {
        "probability": round(probability, 4),
        "threshold": round(threshold, 4),
        "observed": round(observed_sharpe, 4),
        "n_trials": int(n_trials or len(trial_sharpes)),
        "n_observations": n,
        "beats_luck": bool(observed_sharpe > threshold),
        "verdict": _verdict(probability),
    }


def _verdict(p: float) -> str:
    if p >= 0.95:
        return "survives selection bias"
    if p >= 0.90:
        return "marginal — more data needed"
    return "indistinguishable from a lucky search"


def purged_walk_forward(n_samples: int, n_folds: int = 5,
                        embargo: int = 10) -> list[tuple[range, range]]:
    """Sequential (train, test) splits with a gap between them.

    Walk-forward, not shuffled k-fold: shuffling lets a model train on next
    month and test on last month, which no live strategy can do. The embargo
    drops `embargo` samples immediately before each test window so a rolling
    indicator computed on the last training day cannot see into the test.
    """
    if n_samples < n_folds * 4 or n_folds < 2:
        return []

    fold = n_samples // (n_folds + 1)
    splits = []
    for i in range(1, n_folds + 1):
        train_end = fold * i
        test_start = min(train_end + embargo, n_samples)
        test_end = min(test_start + fold, n_samples)
        if test_end - test_start < 5 or train_end < 5:
            continue
        splits.append((range(0, train_end), range(test_start, test_end)))
    return splits
