"""
tests/test_portfolio_risk.py
============================
Portfolio, factor, currency, concentration and liquidity risk, and the
pre-trade decision built on them (src/risk/*).

The market is SYNTHETIC and deterministic: returns built from known factors
with known loadings, so a measured beta can be checked against the beta that
generated it, and a correlated cluster against the correlation that was put
there. Frames run past the decision instant T, so a test can rewrite the
future and prove the decision at T does not move — and rewrite the past and
prove it does.
"""
import math
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

pytest.importorskip("exchange_calendars")

from src.markets import calendars, providers, sessions as S                  # noqa: E402
from src.risk import covariance as COV, decisions as DEC, factors as FAC      # noqa: E402
from src.risk import fx as FX, portfolio_risk as PR, pretrade as PT           # noqa: E402
from src.risk.market_data import FrameMarketData                              # noqa: E402
from src.risk.pretrade import ProposedOrder, Verdict                         # noqa: E402
from src.trading.account import AccountState, Holding                         # noqa: E402

UTC = timezone.utc
T = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)          # Tuesday, after the NY close
DATES = pd.bdate_range("2024-09-02", "2026-12-31")


@pytest.fixture(autouse=True)
def _chain():
    providers.default_chain()
    calendars.clear_cache()
    yield
    providers.default_chain()


def build_frames(seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    n = len(DATES)

    def N(s):
        return rng.normal(0.0, s, n)
    m, tech, cmdty = rng.normal(0.0003, 0.010, n), N(0.008), N(0.010)
    size, val, mom, qual, lowv = N(0.004), N(0.004), N(0.004), N(0.004), N(0.004)
    rates, credit, usd, crypto = N(0.004), N(0.003), N(0.003), N(0.030)
    hedge = -1.0 * m + N(0.004)
    r = {
        "ACWI": m + N(0.0005), "SPY": m + N(0.0005), "IWM": m + size, "IWD": m + val,
        "IWF": m - val, "MTUM": m + mom, "QUAL": m + qual, "USMV": m + lowv,
        "IEF": rates, "HYG": rates + credit, "UUP": usd, "DBC": cmdty, "BTC-USD": crypto,
        "AAPL": 1.1 * m + 0.9 * tech + N(0.006), "MSFT": 1.0 * m + 0.9 * tech + N(0.006),
        "NVDA": 1.4 * m + 1.0 * tech + N(0.008), "XOM": 0.8 * m + 0.6 * cmdty + N(0.012),
        "SH": hedge, "7203.T": 0.9 * m + N(0.010), "BARC.L": 1.2 * m + N(0.012),
        "USDJPY=X": N(0.005), "GBPUSD=X": N(0.004), "EURUSD=X": N(0.004),
    }
    start = {"USDJPY=X": 150.0, "GBPUSD=X": 1.27, "EURUSD=X": 1.08, "7203.T": 2500.0,
             "BARC.L": 250.0, "BTC-USD": 60000.0}
    frames = {}
    for s, x in r.items():
        p = start.get(s, 100.0) * np.exp(np.cumsum(x))
        frames[s] = pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": p,
                                  "Volume": 5e6}, index=DATES)
    return frames


@pytest.fixture
def world():
    frames = build_frames()
    return frames, FrameMarketData(frames)


def px(frames, sym, at=T):
    """The last close of `sym` known at `at` (the price a desk would see)."""
    f = FrameMarketData(frames).history(sym, at)
    return float(f["Close"].iloc[-1])


def H(frames, sym, qty, *, currency="", sector="", ac="equity"):
    return Holding(sym, qty, avg_price=px(frames, sym), market_price=px(frames, sym),
                   asset_class=ac, sector=sector, currency=currency)


def acct(holdings=(), equity=100_000.0, cash=None, as_of=T, ccy="USD", pending=()):
    cash = equity - sum(h.market_value for h in holdings) if cash is None else cash
    return AccountState(known=True, currency=ccy, equity=equity, cash=cash,
                        available_cash=cash, holdings=tuple(holdings),
                        pending_orders=tuple(pending), as_of=as_of, source="test")


def order(frames, sym, qty, side="buy", **kw):
    p = kw.pop("price", None) or px(frames, sym)
    return ProposedOrder(symbol=sym, side=side, qty=qty, price=p, price_at=T, **kw)


LOOSE = PT.PortfolioRiskLimits(max_name_pct=100, max_sector_pct=100, max_gross_pct=1000,
                               max_correlated_positions=99,
                               max_single_foreign_currency_pct=100,
                               max_total_foreign_currency_pct=100,
                               max_single_foreign_country_pct=100, max_portfolio_vol_pct=1000,
                               max_market_beta=100, max_adv_participation_pct=100)


def strip(v):
    d = v.to_dict()
    d.pop("id"), d.pop("timings_ms")
    return d


# ═══ B1 portfolio state ═════════════════════════════════════════════════════

def test_the_one_portfolio_record_has_every_exposure():
    a = AccountState(known=True, equity=10_000, cash=4_000, available_cash=3_500,
                     holdings=(Holding("AAPL", 10, 100, 120), Holding("MSFT", -5, 300, 280),
                               Holding("XOM", 20, 50, 55)),
                     pending_orders=({"symbol": "NVDA", "side": "buy", "notional": 500.0},
                                     {"symbol": "AAPL", "side": "sell", "notional": 240.0}))
    assert a.long_exposure == 10 * 120 + 20 * 55
    assert a.short_exposure == 5 * 280
    assert a.net_exposure == 10 * 120 - 5 * 280 + 20 * 55
    assert a.gross_exposure == 10 * 120 + 5 * 280 + 20 * 55
    assert a.reserved_capital == 500.0 and a.buying_power == 3_500
    assert a.unrealised_pnl == 10 * 20 + (-5) * (-20) + 20 * 5
    d = a.to_dict()
    assert {"net_exposure", "long_exposure", "short_exposure", "reserved_capital",
            "buying_power", "leverage"} <= set(d)


def test_the_legacy_broker_book_keeps_signs_and_fails_closed():
    from src.execution.broker_base import AccountInfo, AssetClass, Position
    from src.trading import account as A

    class B:
        name = "b"

        def get_account(self):
            return AccountInfo("b", cash=1000.0, portfolio_value=5000.0, buying_power=1000.0)

        def get_positions(self):
            return [Position("AAPL", 10, 100, 1200, 200, "long"),
                    Position("TSLA", 3, 200, 600, 0, "short", AssetClass.EQUITY)]

        def get_open_orders(self):
            return [{"ticker": "MSFT", "side": "buy", "qty": 2, "limit_price": 300.0}]
    s = A.from_legacy_broker(B(), now=T)
    assert s.known and s.equity == 5000 and s.reserved_capital == 600.0
    assert {h.symbol: h.qty for h in s.holdings} == {"AAPL": 10, "TSLA": -3}
    assert s.short_exposure == pytest.approx(600)

    class Broken(B):
        def get_positions(self):
            raise ConnectionError("down")
    assert A.from_legacy_broker(Broken(), now=T).known is False


# ═══ B2 currency ════════════════════════════════════════════════════════════

def test_pence_is_a_quoting_unit_not_a_currency(world):
    frames, data = world
    assert FX.rate("GBp", "GBP", at=T, data=data).rate == pytest.approx(0.01)
    g = FX.rate("GBp", "USD", at=T, data=data)
    assert g.rate == pytest.approx(px(frames, "GBPUSD=X") * 0.01)
    assert g.pairs == ("GBPUSD=X",)


def test_direct_inverse_and_cross_rates_carry_their_provenance(world):
    frames, data = world
    usdjpy = px(frames, "USDJPY=X")
    j = FX.rate("JPY", "USD", at=T, data=data)
    assert j.rate == pytest.approx(1 / usdjpy) and "inverted" in j.pairs[0]
    assert j.as_of and j.source.startswith("src/v5/marketdata")
    cross = FX.rate("JPY", "GBP", at=T, data=data)
    assert cross.rate == pytest.approx((1 / usdjpy) / px(frames, "GBPUSD=X"))
    assert len(cross.pairs) == 2 and "via USD" in cross.source
    assert FX.rate("USD", "USD", at=T, data=data).source == "identity"


def test_fx_is_point_in_time_and_the_future_cannot_move_it(world):
    frames, data = world
    before = FX.rate("JPY", "USD", at=T, data=data)
    # FX daily bars are known only after the New York day ends: at 21:00 UTC
    # on the 6th the 6th's bar is not yet known — the rate is the 5th's.
    assert before.as_of == "2026-10-05"
    frames["USDJPY=X"].loc[frames["USDJPY=X"].index >= "2026-10-06", "Close"] = 1.0
    assert FX.rate("JPY", "USD", at=T, data=data) == before
    frames["USDJPY=X"].loc["2026-10-05", "Close"] = 1.0           # the past: must move
    assert FX.rate("JPY", "USD", at=T, data=data).rate == pytest.approx(1.0)


def test_stale_missing_and_unknown_fx_are_not_rates(world):
    frames, data = world
    frames["USDJPY=X"] = frames["USDJPY=X"].loc[:"2026-09-20"]       # feed died
    stale = FX.rate("JPY", "USD", at=T, data=data)
    assert not stale.ok and "stale" in stale.why
    assert not FX.rate("ZAR", "USD", at=T, data=data).ok             # never loaded
    assert not FX.rate("", "USD", at=T, data=data).ok
    with pytest.raises(ValueError):
        FX.rate("JPY", "USD", at=T.replace(tzinfo=None), data=data)


def test_a_historical_position_is_valued_at_its_own_dates_rate(world):
    frames, data = world
    then = datetime(2025, 6, 3, 21, 0, tzinfo=UTC)
    a = acct([H(frames, "7203.T", 100, currency="JPY")], as_of=then)
    v = PR.value(a, at=then, data=data)
    rate = 1 / float(frames["USDJPY=X"].loc["2025-06-02", "Close"])
    assert v.positions[0].value_base == pytest.approx(100 * px(frames, "7203.T") * rate)
    assert v.positions[0].fx["as_of"] == "2025-06-02"


# ═══ B5 covariance ══════════════════════════════════════════════════════════

def test_one_venue_is_daily_several_venues_are_weekly(world):
    frames, data = world
    assert COV.panel(["AAPL", "MSFT"], base="USD", at=T, data=data).frequency == "daily"
    p = COV.panel(["AAPL", "7203.T"], base="USD", at=T, data=data)
    assert p.frequency == "weekly" and len(p.returns) >= COV.MIN_OBS["weekly"]


def test_insufficient_history_is_excluded_not_shortened(world):
    frames, data = world
    frames["XOM"] = frames["XOM"].loc["2026-08-01":]
    p = COV.panel(["AAPL", "XOM"], base="USD", at=T, data=data)
    assert "XOM" in p.excluded and "needed" in p.excluded["XOM"]
    assert list(p.returns.columns) == ["AAPL"]
    empty = COV.covariance(COV.panel(["XOM"], base="USD", at=T, data=data))
    assert not empty.ok and empty.why


def test_a_singular_sample_is_made_stable(world):
    frames, data = world
    frames["MSFT"] = frames["AAPL"].copy()                          # perfectly collinear
    c = COV.covariance(COV.panel(["AAPL", "MSFT", "NVDA"], base="USD", at=T, data=data))
    w = np.linalg.eigvalsh(c.matrix.to_numpy())
    assert c.ok and w.min() > 0 and math.isfinite(c.condition_number)
    assert 0 < c.shrinkage <= 1
    assert np.allclose(c.matrix.to_numpy(), c.matrix.to_numpy().T)


def test_foreign_returns_are_measured_in_the_base_currency(world):
    frames, data = world
    usd = COV.covariance(COV.panel(["7203.T"], base="USD", at=T, data=data)).vol("7203.T")
    jpy = COV.covariance(COV.panel(["7203.T"], base="JPY", at=T, data=data)).vol("7203.T")
    assert usd and jpy and abs(usd - jpy) > 1e-4          # the yen is part of the risk


# ═══ B6 volatility and contributions ═══════════════════════════════════════

def test_two_assets_match_the_closed_form_and_contributions_sum(world):
    frames, data = world
    c = COV.covariance(COV.panel(["AAPL", "XOM"], base="USD", at=T, data=data))
    w = {"AAPL": 0.3, "XOM": 0.2}
    rc = PR.risk_contributions(w, c)
    m = c.matrix.loc[["AAPL", "XOM"], ["AAPL", "XOM"]].to_numpy()
    wv = np.array([0.3, 0.2])
    assert rc["portfolio_vol"] == pytest.approx(math.sqrt(wv @ m @ wv), rel=1e-6)
    assert rc["sum_components"] == pytest.approx(rc["portfolio_vol"], abs=1e-7)
    assert rc["marginal"]["AAPL"] == pytest.approx((m @ wv)[0] / rc["portfolio_vol"], rel=1e-5)
    assert sum(rc["component_pct"].values()) == pytest.approx(100.0, abs=1e-3)


def test_effective_bets_counts_independent_risks_not_names(world):
    frames, data = world
    rng = np.random.default_rng(1)
    for i in range(4):
        p = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(DATES))))
        frames[f"UNC{chr(65 + i)}"] = pd.DataFrame({"Close": p, "Volume": 1e6}, index=DATES)
    ind = COV.covariance(COV.panel([f"UNC{chr(65 + i)}" for i in range(4)], base="USD", at=T, data=data))
    rc = PR.risk_contributions({f"UNC{chr(65 + i)}": 0.25 for i in range(4)}, ind)
    assert rc["effective_bets"] == pytest.approx(4.0, rel=0.2)
    tech = COV.covariance(COV.panel(["AAPL", "MSFT", "NVDA"], base="USD", at=T, data=data))
    rt = PR.risk_contributions({"AAPL": 1 / 3, "MSFT": 1 / 3, "NVDA": 1 / 3}, tech)
    assert rt["effective_bets"] < 1.5                    # three names, about one bet


# ═══ B3 factors ═════════════════════════════════════════════════════════════

def test_measured_betas_recover_the_betas_that_made_the_data(world):
    frames, data = world
    f = FAC.exposures(["AAPL", "NVDA", "XOM"], base="USD", at=T, data=data)
    assert f["AAPL"].exposures["market"].beta == pytest.approx(1.1, abs=0.15)
    assert f["NVDA"].exposures["market"].beta == pytest.approx(1.4, abs=0.15)
    assert f["XOM"].exposures["commodities"].beta == pytest.approx(0.6, abs=0.15)
    assert f["XOM"].exposures["market"].beta == pytest.approx(0.8, abs=0.15)
    assert f["AAPL"].residual_vol and f["AAPL"].exposures["market"].n >= 26


def test_factors_apply_only_where_they_mean_something():
    assert "value" in FAC.applicable("AAPL")[0]
    toyota, why = FAC.applicable("7203.T")
    assert "value" not in toyota and "market" in toyota and "styles are unavailable" in why
    assert FAC.applicable("AAPL261218C00200000")[0] == ()
    assert FAC.applicable("ES=F")[0] == ("market",)
    assert FAC.applicable("ZN=F")[0] == ("rates",)
    assert FAC.applicable("CL=F")[0] == ("commodities",)
    assert FAC.applicable("EURUSD=X")[0] == ("fx",)
    assert FAC.applicable("BTC-USD")[0] == ("market", "crypto")


def test_a_missing_proxy_is_unavailable_not_imputed(world):
    frames, data = world
    del frames["MTUM"]
    f = FAC.exposures(["AAPL"], base="USD", at=T, data=data)
    mom = f["AAPL"].exposures["momentum"]
    assert not mom.ok and "MTUM" in mom.why
    assert f["AAPL"].exposures["value"].ok


def test_portfolio_factor_exposure_states_its_coverage(world):
    frames, data = world
    per = FAC.exposures(["AAPL", "NVDA", "AAPL261218C00200000"], base="USD", at=T, data=data)
    out = FAC.portfolio_exposures({"AAPL": 0.2, "NVDA": 0.2, "AAPL261218C00200000": 0.1}, per)
    assert out["market"]["coverage"] == pytest.approx(0.8)
    assert out["market"]["complete"] is False
    exp = 0.2 * per["AAPL"].exposures["market"].beta + 0.2 * per["NVDA"].exposures["market"].beta
    assert out["market"]["exposure"] == pytest.approx(exp, abs=1e-6)


# ═══ B4 concentration ═══════════════════════════════════════════════════════

def test_concentration_by_every_dimension(world):
    frames, data = world
    hs = [H(frames, "AAPL", 100, sector="Tech"), H(frames, "MSFT", 50, sector="Tech"),
          H(frames, "XOM", 40, sector="Energy"),
          H(frames, "7203.T", 1000, currency="JPY", sector="Autos"),
          H(frames, "BARC.L", 2000, currency="GBp", sector="Banks")]
    v = PR.value(acct(hs), at=T, data=data)
    c = PR.concentration(v)
    values = sorted((abs(p.value_base) for p in v.positions), reverse=True)
    assert c["largest_pct"] == pytest.approx(values[0] / 1000, abs=1e-4)
    assert c["top5_pct"] == pytest.approx(sum(values) / 1000, abs=1e-4)
    assert set(c["by_country"]) == {"US", "JP", "GB"}
    assert set(c["by_currency"]) == {"USD", "JPY", "GBP"}
    assert c["by_sector"]["Tech"] == pytest.approx(sum(values[i] for i, p in enumerate(
        sorted(v.positions, key=lambda p: -abs(p.value_base))) if p.sector == "Tech") / 1000,
        abs=1e-4)
    assert c["effective_names"] > 1


def test_three_tech_names_are_one_correlated_cluster_and_a_hedge_is_not(world):
    frames, data = world
    c = COV.covariance(COV.panel(["AAPL", "MSFT", "NVDA", "XOM"], base="USD", at=T, data=data))
    corr = COV.correlation(c)
    cl = PR.correlated_clusters({"AAPL": .1, "MSFT": .1, "NVDA": .1, "XOM": .1}, corr)
    assert [x["members"] for x in cl] == [["AAPL", "MSFT", "NVDA"]]
    assert PR.correlated_clusters({"AAPL": .1, "MSFT": -.1}, corr) == []


def test_the_full_report(world):
    frames, data = world
    v = PR.value(acct([H(frames, "AAPL", 100), H(frames, "NVDA", 50),
                       H(frames, "7203.T", 2000, currency="JPY")]), at=T, data=data)
    r = PR.analyze(v, at=T, data=data).to_dict()
    assert r["volatility"]["ok"] and r["factor_volatility"]["ok"]
    assert r["exposures"]["gross_pct"] > 0 and r["covariance"]["frequency"] == "weekly"
    assert r["factor_exposures"]["market"]["coverage"] == pytest.approx(1.0)
    assert "value" not in r["instrument_factors"]["7203.T"]["exposures"]
    bare = PR.analyze(v, at=T, data=None).to_dict()
    assert bare["volatility"]["ok"] is False and "no market data" in bare["volatility"]["why"]


# ═══ B7/B8/B9 the pre-trade decision ═══════════════════════════════════════

def test_accept_when_everything_fits(world):
    frames, data = world
    v = PT.assess(order(frames, "XOM", 10), acct(), at=T, limits=PT.PortfolioRiskLimits(),
                  data=data)
    assert v.verdict == Verdict.ACCEPT and v.approved_qty == 10
    assert v.after["name_pct"] > 0 and v.liquidity["participation_pct"] > 0
    assert v.factors["instrument"]["exposures"]["market"]["ok"]


def test_reduce_size_to_exactly_what_the_name_limit_allows(world):
    frames, data = world
    p = px(frames, "AAPL")
    lim = replace(LOOSE, max_name_pct=10.0)
    v = PT.assess(order(frames, "AAPL", 1000), acct([H(frames, "AAPL", 20)]), at=T,
                  limits=lim, data=data)
    assert v.verdict == Verdict.REDUCE_SIZE and v.binding == "name"
    assert v.approved_qty == math.floor((10_000 - 20 * p) / p)
    assert v.after["name_pct"] <= 10.0


@pytest.mark.parametrize("cap,lim_kw,sym,held,kw", [
    ("sector", {"max_sector_pct": 8.0}, "MSFT", [("AAPL", 30, "Tech")], {"sector": "Tech"}),
    ("gross", {"max_gross_pct": 12.0}, "XOM", [("AAPL", 100, "Tech")], {}),
    ("currency", {"max_single_foreign_currency_pct": 4.0}, "7203.T", [], {}),
    ("country", {"max_single_foreign_country_pct": 3.0}, "7203.T", [], {}),
    ("liquidity", {"max_adv_participation_pct": 0.0001}, "XOM", [], {}),
])
def test_each_limit_can_be_the_binding_one(world, cap, lim_kw, sym, held, kw):
    frames, data = world
    lim = replace(LOOSE, **lim_kw)
    hs = [H(frames, s, q, sector=sec) for s, q, sec in held]
    v = PT.assess(order(frames, sym, 5000, **kw), acct(hs), at=T, limits=lim, data=data)
    assert v.verdict == Verdict.REDUCE_SIZE and v.binding == cap, (v.binding, v.caps)
    assert 0 < v.approved_qty < 5000


def test_the_volatility_limit_is_solved_exactly(world):
    frames, data = world
    lim = replace(LOOSE, max_portfolio_vol_pct=4.0)
    v = PT.assess(order(frames, "NVDA", 5000), acct([H(frames, "AAPL", 50)]), at=T,
                  limits=lim, data=data)
    assert v.verdict == Verdict.REDUCE_SIZE and v.binding == "volatility"
    assert v.after["portfolio_vol"] <= 0.04 + 1e-9
    one_more = PT.assess(order(frames, "NVDA", v.approved_qty + 1), acct([H(frames, "AAPL", 50)]),
                         at=T, limits=lim, data=data)
    assert one_more.approved_qty == v.approved_qty


def test_a_trade_that_lowers_volatility_is_not_blocked_by_the_vol_limit(world):
    frames, data = world
    lim = replace(LOOSE, max_portfolio_vol_pct=1.0)                 # already far above
    book = acct([H(frames, "NVDA", 400)])
    worse = PT.assess(order(frames, "AAPL", 10), book, at=T, limits=lim, data=data)
    better = PT.assess(order(frames, "SH", 10), book, at=T, limits=lim, data=data)
    assert worse.verdict == Verdict.REJECT and worse.binding == "volatility"
    assert better.verdict == Verdict.ACCEPT
    assert better.after["portfolio_vol"] < better.before["portfolio_vol"]


def test_the_market_beta_limit(world):
    frames, data = world
    lim = replace(LOOSE, max_market_beta=0.3)
    v = PT.assess(order(frames, "NVDA", 5000), acct(), at=T, limits=lim, data=data)
    assert v.binding == "market_beta" and v.after["market_beta"] <= 0.3 + 1e-9


def test_reject_when_a_limit_is_already_full(world):
    frames, data = world
    lim = replace(LOOSE, max_name_pct=5.0)
    over = math.ceil(5_000 / px(frames, "AAPL")) + 1          # already past 5% of 100k
    v = PT.assess(order(frames, "AAPL", 1), acct([H(frames, "AAPL", over)]), at=T,
                  limits=lim, data=data)
    assert v.verdict == Verdict.REJECT and v.approved_qty == 0 and "no size fits" in " ".join(v.reasons)


def test_reject_correlated_stacking(world):
    frames, data = world
    lim = replace(LOOSE, max_correlated_positions=2)
    v = PT.assess(order(frames, "MSFT", 5), acct([H(frames, "AAPL", 10), H(frames, "NVDA", 10)]),
                  at=T, limits=lim, data=data)
    assert v.verdict == Verdict.REJECT and v.checks["correlated_positions"]["ok"] is False
    ok = PT.assess(order(frames, "XOM", 5), acct([H(frames, "AAPL", 10), H(frames, "NVDA", 10)]),
                   at=T, limits=lim, data=data)
    assert ok.verdict == Verdict.ACCEPT


def test_reject_a_disallowed_asset_class_and_a_false_reduce_only(world):
    frames, data = world
    lim = replace(LOOSE, allowed_asset_classes=("equity",))
    v = PT.assess(order(frames, "BTC-USD", 0.01, asset_class="crypto"), acct(), at=T,
                  limits=lim, data=data)
    assert v.verdict == Verdict.REJECT and "not allowed" in v.reasons[0]
    fake = PT.assess(order(frames, "AAPL", 5, side="sell", reduce_only=True), acct(), at=T,
                     limits=LOOSE, data=data)
    assert fake.verdict == Verdict.REJECT and "would not reduce" in fake.reasons[0]


def test_reducing_a_position_is_accepted_even_over_every_limit(world):
    frames, data = world
    lim = replace(LOOSE, max_name_pct=1.0, max_gross_pct=1.0)
    v = PT.assess(order(frames, "AAPL", 50, side="sell"), acct([H(frames, "AAPL", 100)]), at=T,
                  limits=lim, data=None)
    assert v.verdict == Verdict.ACCEPT and v.approved_qty == 50


# ═══ B14 failure modes are conservative ═════════════════════════════════════

def _fail(frames, data, case):
    o = order(frames, "XOM", 10)
    a = acct()
    d = data
    if case == "missing price":
        o = replace(o, price=None)
    elif case == "stale price":
        o = replace(o, price_at=T - timedelta(minutes=10))
    elif case == "untimed price":
        o = replace(o, price_at=None)
    elif case == "missing FX":
        o = order(frames, "7203.T", 10)
        del frames["USDJPY=X"]
    elif case == "stale FX":
        o = order(frames, "7203.T", 10)
        frames["USDJPY=X"] = frames["USDJPY=X"].loc[:"2026-09-01"]
    elif case == "no FX without market data":
        o, d = order(frames, "7203.T", 10), None
    elif case == "invalid currency":
        o = replace(o, currency="XXX")
    elif case == "unknown currency":
        o = order(frames, "XOM", 10, currency=None)
        o = replace(o, symbol="UNKNOWNTICKER123")
    elif case == "unvalued holding":
        a = acct([H(frames, "7203.T", 10, currency="JPY")])
        d = None
    elif case == "stale portfolio snapshot":
        a = acct(as_of=T - timedelta(minutes=30))
    elif case == "unknown portfolio":
        a = AccountState(known=False, why_unknown="broker down")
    elif case == "wide spread":
        o = replace(o, spread_bps=500.0)
    elif case == "UNKNOWN session":
        return PT.assess(o, a, at=T, limits=LOOSE, data=d,
                         session=S.session("AAPL", datetime(2031, 3, 4, 15, 0, tzinfo=UTC)))
    elif case == "unknown venue":
        return PT.assess(replace(o, symbol="ENI.MI"), a, at=T, limits=LOOSE, data=d,
                         session=S.session("ENI.MI", T))
    return PT.assess(o, a, at=T, limits=LOOSE, data=d)


@pytest.mark.parametrize("case", [
    "missing price", "stale price", "untimed price", "missing FX", "stale FX",
    "no FX without market data", "invalid currency", "unknown currency", "unvalued holding",
    "stale portfolio snapshot", "unknown portfolio", "wide spread", "UNKNOWN session",
    "unknown venue"])
def test_every_unknown_defers(world, case):
    frames, data = world
    v = _fail(frames, data, case)
    assert v.verdict == Verdict.DEFER and v.approved_qty == 0, (case, v.reasons)


@pytest.mark.parametrize("case", ["missing factor", "insufficient history", "no volume",
                                  "no market data"])
def test_missing_estimates_shrink_the_position_instead(world, case):
    frames, data = world
    lim = replace(LOOSE, max_name_pct=10.0)
    d = data
    if case == "missing factor":
        del frames["ACWI"]
    elif case == "insufficient history":
        frames["XOM"] = frames["XOM"].loc["2026-09-01":]
    elif case == "no volume":
        frames["XOM"] = frames["XOM"].drop(columns=["Volume"])
    elif case == "no market data":
        d = None
    v = PT.assess(order(frames, "XOM", 100_000), acct(), at=T, limits=lim, data=d)
    if case == "missing factor":           # vol and liquidity are known: the name limit binds
        assert v.binding == "name" and not v.factors["instrument"]["exposures"]["market"]["ok"]
        assert "market_beta" not in v.caps
    else:
        assert v.binding == "unknown_risk", (case, v.caps)
        assert v.after["name_pct"] <= 5.0 + 1e-9
        assert any("conservative" in r for r in v.reasons)


# ═══ B10 global sessions ════════════════════════════════════════════════════

def test_a_tokyo_signal_at_night_is_deferred_with_a_plan_not_discarded(world):
    frames, data = world
    night = S.session("7203.T", T)                       # 06:00 Tokyo, before the open
    assert not night.tradable
    v = PT.assess(order(frames, "7203.T", 100), acct(), at=T, limits=LOOSE, data=data,
                  session=night)
    assert v.verdict == Verdict.DEFER and v.plan["kind"] == "SCHEDULED"
    assert v.plan["valid_from"] > T.isoformat()


def test_at_the_tokyo_open_the_yen_exposure_counts(world):
    frames, data = world
    t = datetime(2026, 10, 7, 0, 30, tzinfo=UTC)        # 09:30 Tokyo
    sess = S.session("7203.T", t)
    assert sess.tradable
    lim = replace(LOOSE, max_single_foreign_currency_pct=2.0)
    o = replace(order(frames, "7203.T", 10_000, price=px(frames, "7203.T", t)), price_at=t)
    v = PT.assess(o, acct(as_of=t), at=t, limits=lim, data=data, session=sess)
    assert v.binding == "currency" and v.after["currency"] == "JPY"
    assert v.after["currency_pct"] <= 2.0 + 1e-6 and v.data["fx"]["ok"]


# ═══ B11 point in time: the future cannot change a past decision ═══════════

def _future_rows(sym, frame):
    from src.markets.sessions import resolve
    venue = resolve(sym).venue
    return [d for d in frame.index if not S.bar_known_by(venue, d.date(), T)]


def _mutate(frames, sym, fn, which):
    f = frames[sym]
    rows = _future_rows(sym, f)
    idx = pd.DatetimeIndex(rows) if which == "future" else f.index[f.index < min(rows)][-40:]
    frames[sym].loc[idx] = fn(f.loc[idx].copy())


MUTATIONS = {
    "price": ("NVDA", lambda f: f.assign(Close=f["Close"] * 3.0)),
    "factor": ("ACWI", lambda f: f.assign(Close=f["Close"][::-1].values)),
    "fx": ("USDJPY=X", lambda f: f.assign(Close=f["Close"] * 0.5)),
    "volatility": ("AAPL", lambda f: f.assign(Close=f["Close"] * (1 + 0.2 * np.sin(np.arange(len(f)))))),
    "corporate_action": ("NVDA", lambda f: f.assign(Close=f["Close"] / 2, Volume=f["Volume"] * 2)),
    "correlation": ("AAPL", lambda f: f.assign(Close=f["Close"].iloc[0] ** 2 / f["Close"])),
    "volume": ("NVDA", lambda f: f.assign(Volume=1.0)),
}


def _decide(frames):
    data = FrameMarketData(frames)
    book = acct([H(frames, "AAPL", 50), H(frames, "7203.T", 1000, currency="JPY")])
    lim = replace(LOOSE, max_portfolio_vol_pct=6.0, max_market_beta=0.5)
    return strip(PT.assess(order(frames, "NVDA", 300), book, at=T, limits=lim, data=data))


@pytest.mark.parametrize("what", list(MUTATIONS))
def test_rewriting_the_future_changes_nothing_and_the_past_changes_something(what):
    sym, fn = MUTATIONS[what]
    base = build_frames()
    before = _decide(base)
    future = build_frames()
    _mutate(future, sym, fn, "future")
    assert _decide(future) == before, what
    past = build_frames()
    _mutate(past, sym, fn, "past")
    assert _decide(past) != before, f"{what}: the check could not fail"


# ═══ B12 paper integration and B13 provenance ═══════════════════════════════

@pytest.mark.usefixtures("clean_trading_state")
def test_a_paper_order_is_sized_by_portfolio_risk_and_the_reason_is_kept(world):
    from test_trading_pipeline import ready_strategy, set_mode
    from trading_helpers import make_quote, make_signal
    from src.trading import costs as _costs
    from src.trading.broker import SimulatedBroker
    from src.trading.pipeline import TradingPipeline
    frames, data = world
    ready_strategy()
    set_mode("PAPER")
    now = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    b = SimulatedBroker(cash=1000.0)
    b.set_quote(make_quote(symbol="AAPL", now=now))
    lim = replace(LOOSE, max_name_pct=4.0)
    pipe = TradingPipeline(b, fee_schedule=_costs.SCHEDULES["simulated"], market_data=data,
                           portfolio_limits=lim)
    res = pipe.run(make_signal(symbol="AAPL", now=now, expected_edge_bps=2000), now=now)
    assert res.submitted, res.reasons
    pr = res.portfolio_risk
    assert pr["verdict"] == "REDUCE_SIZE" and pr["binding"] == "name"
    assert b.positions["AAPL"]["qty"] == pytest.approx(pr["approved_qty"])
    assert res.decision["sizing"]["binding_constraint"] == "portfolio_risk"
    rec = DEC.query(symbol="AAPL", path="pipeline")[0]
    assert rec["id"] == pr["id"] and rec["verdict"] == "REDUCE_SIZE"
    for key in ("reasons", "before", "after", "caps", "binding", "data", "limits", "factors"):
        assert rec[key] is not None, key
    assert rec["data"]["portfolio_as_of"] and rec["data"]["price_at"]


@pytest.mark.usefixtures("clean_trading_state")
def test_a_closed_market_in_the_pipeline_leaves_a_plan_and_sends_nothing():
    from test_trading_pipeline import broker, pipeline, ready_strategy, set_mode
    from trading_helpers import make_quote, make_signal
    ready_strategy()
    set_mode("PAPER")
    sat = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    b = broker()
    b.set_quote(make_quote(now=sat))
    res = pipeline(b).run(make_signal(now=sat), now=sat)
    assert res.submitted is False and res.stage_reached == "session"
    assert res.plan["kind"] == "SCHEDULED" and b.submit_calls == 0


def test_execution_time_revalidation_uses_the_book_as_it_is_at_send(tmp_path, monkeypatch, world):
    """Queued against an empty book; by the time it is sent the account
    already holds the name up to its limit. Then: room for half."""
    from src.execution import approval_queue as aq
    from src.execution.broker_base import AccountInfo, OrderRequest, OrderResult, OrderSide, \
        OrderStatus, Position
    from src.execution.order_manager import OrderManager
    frames, _ = world
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    now = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)

    class Book:
        name = "book"
        paper = True

        def __init__(self, held):
            self.held, self.sent = held, []

        def is_connected(self):
            return True

        def get_account(self):
            return AccountInfo("book", cash=50_000.0, portfolio_value=100_000.0,
                               buying_power=50_000.0)

        def get_positions(self):
            return [Position("AAPL", self.held, 100.0, self.held * 100.0, 0.0, "long")] \
                if self.held else []

        def get_open_orders(self):
            return []

        def get_quote(self, t):
            return {"bid": 99.99, "ask": 100.0, "mid": 100.0, "timestamp": now.isoformat()}

        def submit_order(self, req):
            self.sent.append(req.qty)
            return OrderResult("x", OrderStatus.FILLED, filled_qty=req.qty, avg_fill_price=100.0)
    # $5,000 of AAPL; with no market data on the send path the unknown-risk
    # limit is half of that, $2,500: 50 held is full, 10 held leaves room for 15
    lim = PT.PortfolioRiskLimits(max_name_pct=5.0)
    for held, expect in ((50, None), (10, 15.0)):
        q = aq.ApprovalQueue().push(OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=40),
                                    broker="alpaca")
        bk = Book(held)
        res = OrderManager(alpaca=bk, portfolio_limits=lim).execute(q.id, now=now)
        if expect is None:
            assert res["ok"] is False and res["risk"]["verdict"] == "REJECT" and bk.sent == []
        else:
            assert res["ok"] and bk.sent == [expect] and res["risk"]["verdict"] == "REDUCE_SIZE"
    assert DEC.query(path="approval_queue", limit=5)


def test_the_decisions_route_is_owner_only_and_serves_the_record(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import app
    from src.auth import hardening
    from src.auth import session as sess
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "test-owner-token-risk")
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.delenv("ARIA_TRUST_PROXY", raising=False)
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "s.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".k")
    sess.SESSIONS._attempts.clear()
    hardening._limiter._hits.clear()
    DEC.record(PT.RiskVerdict(Verdict.DEFER, "ZZZ", "buy", 1, 0, T.isoformat(),
                              reasons=["test"]), path="test")
    c = TestClient(app, client=("203.0.113.41", 53301))
    assert c.get("/api/trading/risk-decisions").status_code in (401, 403)
    assert c.post("/api/auth/login", json={"token": "test-owner-token-risk"}).status_code == 200
    body = c.get("/api/trading/risk-decisions", params={"symbol": "ZZZ"}).json()
    assert body["decisions"][0]["verdict"] == "DEFER" and body["decisions"][0]["reasons"] == ["test"]


# ═══ B15 performance ════════════════════════════════════════════════════════

def test_the_send_time_check_is_fast_and_the_full_check_is_bounded(world):
    import time
    frames, data = world
    book = acct([H(frames, s, 10) for s in ("AAPL", "MSFT", "XOM")])
    PT.assess(order(frames, "NVDA", 10), book, at=T, limits=LOOSE, data=None)
    t0 = time.perf_counter()
    for _ in range(50):
        PT.assess(order(frames, "NVDA", 10), book, at=T, limits=LOOSE, data=None)
    exposure_only = (time.perf_counter() - t0) / 50
    t0 = time.perf_counter()
    PT.assess(order(frames, "NVDA", 10), book, at=T, limits=LOOSE, data=data)
    full = time.perf_counter() - t0
    assert exposure_only < 0.05, f"{exposure_only * 1e3:.1f} ms"
    assert full < 10.0, f"{full:.2f} s"
