"""
options_analytics.py — GS-Quant Bridge: Options Analytics
Full Black-Scholes pricing engine with all Greeks.
Works standalone — no GS API, no external dependencies beyond math.
scipy is used for norm CDF if available; bisection method as fallback.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple


# ── Normal distribution fallback (if scipy unavailable) ──────────────────────

def _erf_approx(x: float) -> float:
    """Abramowitz & Stegun approximation for erf, max error < 1.5e-7."""
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = (((( 1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592
    return sign * (1 - y * math.exp(-x * x))

def _norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    try:
        from scipy.stats import norm
        return norm.cdf(x)
    except ImportError:
        return 0.5 * (1 + _erf_approx(x / math.sqrt(2)))

def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float   # per calendar day
    vega: float    # per 1% IV move
    rho: float     # per 1% rate move
    iv: Optional[float] = None
    intrinsic: float = 0.0
    time_value: float = 0.0

    def to_dict(self) -> dict:
        return {
            "price": round(self.price, 4),
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 4),
            "theta": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "rho": round(self.rho, 4),
            "iv": round(self.iv, 4) if self.iv else None,
            "intrinsic": round(self.intrinsic, 4),
            "time_value": round(self.time_value, 4),
        }

    def summary(self) -> str:
        iv_str = f" | IV: {self.iv:.1%}" if self.iv else ""
        return (
            f"Price: ${self.price:.2f} | Δ: {self.delta:.3f} | Γ: {self.gamma:.4f} | "
            f"Θ: ${self.theta:.3f}/day | Vega: ${self.vega:.3f}/1%{iv_str}"
        )


@dataclass
class VolSurface:
    spot: float
    strikes: list
    expiries: list  # in years
    ivs: dict       # {(strike, expiry): iv}

    def get_iv(self, strike: float, expiry: float) -> Optional[float]:
        return self.ivs.get((strike, expiry))

    def skew(self, expiry: float) -> Optional[float]:
        """Put-call skew: 25-delta put IV minus 25-delta call IV (approximated from strikes)."""
        ivs_at_expiry = {k: v for (k, e), v in self.ivs.items() if e == expiry}
        if len(ivs_at_expiry) < 2:
            return None
        sorted_strikes = sorted(ivs_at_expiry.keys())
        otm_put_iv = ivs_at_expiry[sorted_strikes[0]]
        otm_call_iv = ivs_at_expiry[sorted_strikes[-1]]
        return otm_put_iv - otm_call_iv  # Positive = downside skew (normal)

    def term_structure(self, strike_near_atm: float) -> list:
        """IV by expiry at the given strike — shows vol term structure."""
        result = []
        for expiry in sorted(set(e for _, e in self.ivs.keys())):
            iv = self.get_iv(strike_near_atm, expiry)
            if iv:
                result.append((expiry * 365, iv))
        return result


# ── Black-Scholes Engine ─────────────────────────────────────────────────────

class BlackScholes:
    """
    Full Black-Scholes options pricer with all five Greeks.
    Supports European calls and puts.
    """

    def __init__(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,   # in years
        risk_free_rate: float,   # annual, decimal (e.g. 0.05 = 5%)
        implied_vol: float,      # annual, decimal (e.g. 0.25 = 25%)
        dividend_yield: float = 0.0,
    ):
        self.S = spot
        self.K = strike
        self.T = max(time_to_expiry, 1e-6)  # Avoid division by zero
        self.r = risk_free_rate
        self.sigma = max(implied_vol, 1e-6)
        self.q = dividend_yield

    def _d1(self) -> float:
        return (
            math.log(self.S / self.K) + (self.r - self.q + 0.5 * self.sigma ** 2) * self.T
        ) / (self.sigma * math.sqrt(self.T))

    def _d2(self) -> float:
        return self._d1() - self.sigma * math.sqrt(self.T)

    def price_and_greeks(self, option_type: str = "call") -> Greeks:
        """
        Calculate option price and all five Greeks.
        option_type: 'call' or 'put'
        """
        d1 = self._d1()
        d2 = self._d2()
        option_type = option_type.lower()

        sqrt_T = math.sqrt(self.T)
        e_qt = math.exp(-self.q * self.T)
        e_rt = math.exp(-self.r * self.T)
        pdf_d1 = _norm_pdf(d1)

        if option_type == "call":
            price = (self.S * e_qt * _norm_cdf(d1) - self.K * e_rt * _norm_cdf(d2))
            delta = e_qt * _norm_cdf(d1)
            rho = self.K * self.T * e_rt * _norm_cdf(d2) / 100
        else:  # put
            price = (self.K * e_rt * _norm_cdf(-d2) - self.S * e_qt * _norm_cdf(-d1))
            delta = -e_qt * _norm_cdf(-d1)
            rho = -self.K * self.T * e_rt * _norm_cdf(-d2) / 100

        gamma = (e_qt * pdf_d1) / (self.S * self.sigma * sqrt_T)
        vega = self.S * e_qt * pdf_d1 * sqrt_T / 100   # Per 1% IV move
        theta_annual = (
            -(self.S * self.sigma * e_qt * pdf_d1) / (2 * sqrt_T)
            - self.r * self.K * e_rt * (_norm_cdf(d2) if option_type == "call" else _norm_cdf(-d2))
            + self.q * self.S * e_qt * (delta if option_type == "call" else -delta + e_qt)
        )
        theta_daily = theta_annual / 365

        # Intrinsic / time value
        intrinsic = max(0, (self.S - self.K) if option_type == "call" else (self.K - self.S))
        time_value = max(0, price - intrinsic)

        return Greeks(
            price=price,
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega,
            rho=rho,
            iv=self.sigma,
            intrinsic=intrinsic,
            time_value=time_value,
        )

    @classmethod
    def implied_vol(
        cls,
        market_price: float,
        spot: float,
        strike: float,
        time_to_expiry: float,
        risk_free_rate: float,
        option_type: str = "call",
        tolerance: float = 1e-5,
        max_iterations: int = 200,
    ) -> Optional[float]:
        """
        Bisection method IV solver.
        Returns IV as a decimal (e.g. 0.25 = 25%) or None if it fails.
        """
        lo, hi = 1e-5, 5.0  # IV between ~0% and 500%

        def price_at_vol(vol):
            bs = cls(spot, strike, time_to_expiry, risk_free_rate, vol)
            return bs.price_and_greeks(option_type).price

        # Check bounds
        try:
            if price_at_vol(lo) > market_price:
                return lo
            if price_at_vol(hi) < market_price:
                return None

            for _ in range(max_iterations):
                mid = (lo + hi) / 2
                mid_price = price_at_vol(mid)
                if abs(mid_price - market_price) < tolerance:
                    return mid
                if mid_price < market_price:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2
        except Exception:
            return None


# ── Vol Surface Builder ────────────────────────────────────────────────────────

class VolSurfaceBuilder:
    """
    Constructs an approximate vol surface using parameterised skew and term structure.
    Useful when you only have ATM IV and want to model the full surface.
    """

    @staticmethod
    def build(
        spot: float,
        atm_iv: float,
        risk_free_rate: float = 0.05,
        skew_slope: float = -0.15,     # IV increases for lower strikes (normal skew)
        term_slope: float = -0.02,     # IV term structure (negative = backwardation)
        strikes_pct: list = None,      # List of % moneyness e.g. [0.85, 0.9, 0.95, 1.0, 1.05, 1.1]
        expiries_days: list = None,    # Days to expiry e.g. [30, 60, 90, 180, 365]
    ) -> VolSurface:
        if strikes_pct is None:
            strikes_pct = [0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15]
        if expiries_days is None:
            expiries_days = [30, 60, 90, 180, 365]

        strikes = [spot * pct for pct in strikes_pct]
        expiries = [d / 365 for d in expiries_days]
        ivs = {}

        for strike, pct in zip(strikes, strikes_pct):
            for expiry, days in zip(expiries, expiries_days):
                moneyness = math.log(spot / strike)
                # Skew: OTM puts have higher IV (negative moneyness = puts)
                skew_adj = skew_slope * moneyness
                # Term structure: shorter expiries often higher IV in backwardation
                term_adj = term_slope * math.sqrt(days / 30)
                iv = max(0.01, atm_iv + skew_adj + term_adj)
                ivs[(strike, expiry)] = iv

        return VolSurface(spot=spot, strikes=strikes, expiries=expiries, ivs=ivs)


# ── Convenience function for ARIA ─────────────────────────────────────────────

def quick_options_summary(
    ticker: str,
    spot: float,
    signal_score: float = 50,
    iv: float = 0.25,
    days_to_expiry: int = 30,
    risk_free_rate: float = 0.05,
) -> str:
    """
    Quick options analysis for a ticker — ARIA calls this for option-related queries.
    Returns a formatted string with key metrics.
    """
    T = days_to_expiry / 365
    atm_strike = round(spot / 5) * 5  # Round to nearest $5

    # ATM call and put
    bs_call = BlackScholes(spot, atm_strike, T, risk_free_rate, iv)
    bs_put = BlackScholes(spot, atm_strike, T, risk_free_rate, iv)
    call = bs_call.price_and_greeks("call")
    put = bs_put.price_and_greeks("put")

    # OTM call (5% above) and OTM put (5% below)
    otm_call_strike = round(spot * 1.05 / 5) * 5
    otm_put_strike = round(spot * 0.95 / 5) * 5
    otm_call = BlackScholes(spot, otm_call_strike, T, risk_free_rate, iv).price_and_greeks("call")
    otm_put = BlackScholes(spot, otm_put_strike, T, risk_free_rate, iv).price_and_greeks("put")

    # Vol surface skew
    vs = VolSurfaceBuilder.build(spot, iv)
    skew = vs.skew(T)
    skew_str = f"{skew:+.1%}" if skew is not None else "N/A"

    # Signal direction
    direction_label = "BULLISH" if signal_score > 60 else "BEARISH" if signal_score < 40 else "NEUTRAL"

    lines = [
        f"=== OPTIONS ANALYTICS: {ticker} ===",
        f"Spot: ${spot:.2f} | IV (30d ATM): {iv:.1%} | Expiry: {days_to_expiry}d | Signal: {direction_label} ({signal_score:.0f}/100)",
        "",
        f"ATM CALL  (K=${atm_strike}): {call.summary()}",
        f"ATM PUT   (K=${atm_strike}): {put.summary()}",
        "",
        f"OTM CALL  (K=${otm_call_strike}, +5%): Price=${otm_call.price:.2f} | Δ={otm_call.delta:.3f}",
        f"OTM PUT   (K=${otm_put_strike}, -5%): Price=${otm_put.price:.2f} | Δ={otm_put.delta:.3f}",
        "",
        f"Skew (OTM put IV - call IV): {skew_str}",
        f"Put/Call parity check: ATM call ${call.price:.2f} vs put ${put.price:.2f}",
        "",
        "ARIA NOTE: These are theoretical BS prices. Actual market prices will differ.",
        "           Use IV surface skew to gauge market's directional bias.",
        "           This is for research purposes only — not a trade recommendation.",
        "=" * 45,
    ]
    return "\n".join(lines)
