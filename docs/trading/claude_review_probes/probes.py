"""Independent reviewer probes against feature/aria-multiasset-trading @7385130.

Read-only: imports the Codex worktree, writes only to a temp directory.
Run: <venv python> probes.py <path-to-worktree>
"""
import sys, tempfile, json
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))

from src.trading.contracts import (AccountSnapshot, AssetClass, DataProvenance, FXConversion,
    Instrument, InstrumentSpecification, MarketQuote, TradeProposal, RiskAssessment, content_hash,
    OHLCVBar, ResearchEvidence, BalanceSnapshot)
from src.trading.multiasset_risk import RiskInputs, assess
from src.trading.rule_profiles import RuleProfile, RuleState, evaluate_profile, rule_day
from src.trading.review_store import ReviewStore, ReviewRefused

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
PRAGUE = ZoneInfo("Europe/Prague")
out = {}


def prov(iid, sym, t=NOW, perm="EXECUTION"):
    return DataProvenance(provider="probe", instrument_id=iid, original_symbol=sym, observed_at=t,
                          retrieved_at=t, data_type="quote", freshness="FRESH", quality="VALID",
                          permission=perm)


def profile(**kw):
    base = dict(provider="FIXTURE_NOT_FTMO", provider_entity="probe", jurisdiction="probe",
        product="TEST", account_id="demo-1", account_size="10000", account_currency="USD",
        evaluation_stage="probe", account_type="demo", account_creation_date="2026-11-01",
        effective_from=NOW - timedelta(days=10), effective_until=NOW + timedelta(days=10),
        source_urls=("https://example.invalid/probe",), verified_at=NOW - timedelta(hours=2),
        user_confirmed_at=NOW - timedelta(hours=1), policy_version="probe-1",
        daily_loss_fraction="0.05", overall_loss_fraction="0.10", drawdown_mode="STATIC",
        daily_method="MIDNIGHT_BALANCE_MINUS_INITIAL_ALLOWANCE", consistency_rule="NONE",
        overnight_rule="probe", weekend_rule="probe", news_rule="probe", instrument_rule="probe",
        prohibited_practices_rule="probe", max_leverage="100", max_server_requests_per_minute=10)
    base.update(kw)
    return RuleProfile(**base)


def make_case(*, sym, base, quote, contract, tick, bid, ask, side, qty, entry, sl, tp,
              conversion=None, equity="10000", balance="10000", daily_balance="10000",
              asset=AssetClass.FOREX, prof=None, tol="0", hw="10000", margin_rate="0.01",
              vmax="50", spread_cost="0", commission="0", slip="0"):
    iid = f"probe:{sym}"
    p = prof or profile()
    instrument = Instrument(instrument_id=iid, provider_symbol=sym, asset_class=asset, product="CFD",
        venue="PROBE", base_currency=base, quote_currency=quote, settlement_currency=quote, calendar="probe")
    spec = InstrumentSpecification(instrument=instrument, contract_size=contract, tick_size=tick,
        tick_value=str(D(contract) * D(tick)), tick_value_currency=quote, volume_min="0.01",
        volume_max=vmax, volume_step="0.01", margin_rate=margin_rate, supported_order_types=("MARKET",),
        quote_max_age_seconds=15, calculation_mode="LINEAR", provenance=prov(iid, sym))
    state = RuleState(account_id="demo-1", account_currency="USD", daily_balance=daily_balance,
        daily_snapshot_at=datetime(2026, 11, 6, tzinfo=PRAGUE), equity=equity,
        balance_high_water=hw, high_water_observed_at=NOW)
    proposal = TradeProposal(proposal_id="p1", account_id="demo-1", instrument_id=iid, side=side,
        order_type="MARKET", quantity=qty, entry=entry, price_tolerance=tol, stop_loss=sl,
        take_profit=tp, expires_at=NOW + timedelta(minutes=1), rule_profile_hash=p.profile_hash,
        market_snapshot_id="q1", hypothesis_id="h1")
    inputs = RiskInputs(account=AccountSnapshot(account_id="demo-1", environment="DEMO", currency="USD",
        balance=balance, equity=equity, available_margin="1000000", observed_at=NOW,
        positions_complete=True, orders_complete=True),
        quote=MarketQuote(bid=bid, ask=ask, snapshot_id="q1", provenance=prov(iid, sym)),
        specification=spec, conversion=conversion, profile=p, rule_state=state,
        existing_open_risk="0", pending_order_risk="0", correlated_risk="0", current_notional="0",
        daily_net_pnl="0", position_count=0, orders_last_minute=0, spread_cost=spread_cost,
        commission=commission, slippage_allowance=slip, financing_allowance="0", session_open=True,
        kill_switch_clear=True, operational_constraints_verified=True, book_risk_complete=True,
        cooldown_clear=True, duplicate_position=False, abnormal_market=False)
    return proposal, inputs


def fx(frm, to, rate, sym="FXRATE"):
    return FXConversion(from_currency=frm, to_currency=to, rate=rate, provenance=prov("fx:" + sym, sym))


def run(name, case):
    r = assess(*case, now=NOW)
    out[name] = {"verdict": r.verdict, "reasons": list(r.reasons),
                 "monetary_risk": str(r.monetary_risk), "quantity": str(r.quantity)}
    return r


# ---- E1 EURUSD: 0.10 lot, 20-pip stop. Expected loss = 0.0020*100000*0.10 = $20.00
run("E1_EURUSD_0.10lot_20pip", make_case(sym="EURUSD", base="EUR", quote="USD", contract="100000",
    tick="0.00001", bid="1.08000", ask="1.08000", side="BUY", qty="0.10", entry="1.08000",
    sl="1.07800", tp="1.08400"))

# ---- E2 USDJPY correct JPY->USD rate 1/150. 0.10 lot, 30-pip stop (0.30 JPY).
# Loss in JPY = 0.30*100000*0.10 = 3000 JPY; USD = 3000/150 = $20.00
run("E2_USDJPY_correct_rate", make_case(sym="USDJPY", base="USD", quote="JPY", contract="100000",
    tick="0.001", bid="150.000", ask="150.000", side="BUY", qty="0.10", entry="150.000",
    sl="149.700", tp="150.600", conversion=fx("JPY", "USD", str(D(1) / D(150)))))

# ---- E3 USDJPY with the USDJPY PRICE supplied as the JPY->USD rate (labels correct, value inverted).
# Expected safe outcome: refuse. Engine multiplies 3000 JPY by 150 => $450,000 "risk" (overstated, DENY),
# and the converse inversion (rate supplied 1/150 where 150 needed) understates 22,500x.
run("E3_USDJPY_inverted_value", make_case(sym="USDJPY", base="USD", quote="JPY", contract="100000",
    tick="0.001", bid="150.000", ask="150.000", side="BUY", qty="0.10", entry="150.000",
    sl="149.700", tp="150.600", conversion=fx("JPY", "USD", "150")))

# ---- E3b EURGBP on a USD account: GBP->USD needed (~1.27). Supply 1/1.27 (USD->GBP value) labelled GBP->USD.
# 1.00 lot, 25-pip stop: loss GBP = 0.0025*100000 = 250 GBP; correct USD = 317.50; engine with 0.7874 => 196.85
r = run("E3b_EURGBP_rate_value_inverted_understates", make_case(sym="EURGBP", base="EUR", quote="GBP",
    contract="100000", tick="0.00001", bid="0.85000", ask="0.85000", side="BUY", qty="0.20",
    entry="0.85000", sl="0.84750", tp="0.85500", conversion=fx("GBP", "USD", "0.7874")))
out["E3b_EURGBP_rate_value_inverted_understates"]["correct_usd_risk"] = str(D("0.0025") * 100000 * D("0.20") * D("1.27"))

# ---- E4 XAUUSD: contract 100 oz. 0.10 lot, $5 stop => 5*100*0.10 = $50 risk; notional 2600*100*0.1 = $26,000
run("E4_XAUUSD_0.10lot_$5stop", make_case(sym="XAUUSD", base="XAU", quote="USD", contract="100",
    tick="0.01", bid="2600.00", ask="2600.00", side="BUY", qty="0.10", entry="2600.00", sl="2595.00",
    tp="2610.00", asset=AssetClass.METALS))

# ---- E5 Gold with contract size mis-specified as 1 (and tick value consistently 0.01): engine cannot tell.
# True risk of 0.10 lot with $5 stop is $50; engine believes $0.50 and the leverage check sees $260 notional.
run("E5_XAUUSD_wrong_contract_size_consistent", make_case(sym="XAUUSD", base="XAU", quote="USD",
    contract="1", tick="0.01", bid="2600.00", ask="2600.00", side="BUY", qty="0.10", entry="2600.00",
    sl="2595.00", tp="2610.00", asset=AssetClass.METALS))
# With contract size wrong, how large a position does the engine allow at 0.5% (=$50)?
r = run("E5b_XAUUSD_wrong_contract_size_10lots", make_case(sym="XAUUSD", base="XAU", quote="USD",
    contract="1", tick="0.01", bid="2600.00", ask="2600.00", side="BUY", qty="10", entry="2600.00",
    sl="2595.00", tp="2610.00", asset=AssetClass.METALS))
out["E5b_XAUUSD_wrong_contract_size_10lots"]["true_risk_if_contract_100"] = str(D(5) * 100 * 10)
out["E5b_XAUUSD_wrong_contract_size_10lots"]["true_notional_if_contract_100"] = str(D(2600) * 100 * 10)

# ---- E6 Index CFD US30 contract 1, 1.00 lot, 50-pt stop => $50; notional 42,000 => 4.2x equity
run("E6_US30_1lot_50pt", make_case(sym="US30", base="USD", quote="USD", contract="1", tick="0.01",
    bid="42000.00", ask="42000.00", side="BUY", qty="1.00", entry="42000.00", sl="41950.00",
    tp="42100.00", asset=AssetClass.INDICES))
# ---- E6b same with profile max_leverage 3? Engine policy caps at min(3, profile) => 3x => $30k max notional
run("E6b_US30_0.70lot", make_case(sym="US30", base="USD", quote="USD", contract="1", tick="0.01",
    bid="42000.00", ask="42000.00", side="BUY", qty="0.70", entry="42000.00", sl="41950.00",
    tp="42100.00", asset=AssetClass.INDICES))

# ---- E7 Exact floor landing: equity 9020, STATIC overall floor 9000. Budget = 20. Risk exactly 20 => ?
run("E7_lands_exactly_on_overall_floor", make_case(sym="EURUSD", base="EUR", quote="USD", contract="100000",
    tick="0.00001", bid="1.08000", ask="1.08000", side="BUY", qty="0.10", entry="1.08000",
    sl="1.07980", tp="1.08400", equity="9020", balance="9020", daily_balance="9020"))

# ---- E8 Daily floor with floating loss carried over midnight: midnight balance 10000 => floor 9500.
# Equity now 9700 (open position -300). Room 200; engine's internal 2% daily stop uses daily_net_pnl (caller).
run("E8_floating_loss_over_midnight", make_case(sym="EURUSD", base="EUR", quote="USD", contract="100000",
    tick="0.00001", bid="1.08000", ask="1.08000", side="BUY", qty="0.10", entry="1.08000",
    sl="1.07800", tp="1.08400", equity="9520", balance="10000", daily_balance="10000"))

# ---- E9 1-Step style 3% daily on FTMO-like numbers; trailing high-water 10800 => overall floor 9800
p_trail = profile(daily_loss_fraction="0.03", drawdown_mode="TRAILING_EOD_BALANCE")
run("E9_trailing_floor_9800_equity_9850", make_case(sym="EURUSD", base="EUR", quote="USD",
    contract="100000", tick="0.00001", bid="1.08000", ask="1.08000", side="BUY", qty="0.10",
    entry="1.08000", sl="1.07800", tp="1.08400", equity="9850", balance="9850", daily_balance="9850",
    hw="10800", prof=p_trail))

# ---- PER-DAY server limit: 1,999 orders today but 0 in the last minute => engine has no per-day counter
out["F1_per_day_limit_field_exists"] = "orders_today" in RiskInputs.model_fields or \
    any("day" in f for f in RiskInputs.model_fields if "order" in f)

# ---- DST rule-day boundaries
for label, t in [("2026-03-28T23:30Z", datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc)),
                 ("2026-03-29T22:30Z", datetime(2026, 3, 29, 22, 30, tzinfo=timezone.utc)),
                 ("2026-10-24T22:30Z", datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc)),
                 ("2026-10-25T23:30Z", datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc))]:
    p = profile()
    d = rule_day(p, t)
    boundary = datetime.combine(d, datetime.min.time(), PRAGUE)
    out[f"DST_{label}"] = {"rule_day": d.isoformat(), "boundary_utc": boundary.astimezone(timezone.utc).isoformat()}

# ---- Review store: forged ALLOW assessment accepted; high-water lowered by profile re-version
tmp = Path(tempfile.mkdtemp(prefix="review_probe_"))
store = ReviewStore(tmp / "r.db")
case = make_case(sym="EURUSD", base="EUR", quote="USD", contract="100000", tick="0.00001",
    bid="1.08000", ask="1.08000", side="BUY", qty="5.00", entry="1.08000", sl="1.07000", tp="1.10000")
proposal = case[0]
forged = RiskAssessment(verdict="ALLOW", reasons=("forged",), policy_hash="x" * 64,
    proposal_hash=content_hash(proposal), assessed_at=NOW, expires_at=NOW + timedelta(seconds=30),
    quantity=proposal.quantity, monetary_risk="1")
try:
    store.queue(proposal, forged, now=NOW)
    token = store.approve("p1", authenticated_owner="anyone-string", now=NOW)
    intent = store.reserve_intent(proposal, token, forged, now=NOW + timedelta(seconds=1))
    true_risk = assess(*case, now=NOW)
    out["R1_forged_allow_reserved_intent"] = {"intent_reserved": bool(intent),
        "true_engine_verdict": true_risk.verdict, "true_reasons": list(true_risk.reasons),
        "true_loss_usd": str(D("0.01") * 100000 * D("5"))}
except ReviewRefused as e:
    out["R1_forged_allow_reserved_intent"] = {"refused": str(e)}

# High-water lineage keyed by profile hash
p1 = profile(drawdown_mode="TRAILING_EOD_BALANCE", account_creation_date="2026-11-01",
             effective_from=datetime(2026, 10, 1, tzinfo=timezone.utc))
s2 = ReviewStore(tmp / "hw.db")
balances = ["10000", "10400", "10800", "10600", "10500", "10450"]
for i, b in enumerate(balances):
    day = date(2026, 11, 1) + timedelta(days=i)
    snap = BalanceSnapshot(account_id="demo-1", currency="USD", balance=b, equity=b,
                           observed_at=datetime.combine(day, datetime.min.time(), PRAGUE))
    s2.record_rule_boundary(p1, snap, evidence_hash="a" * 64, now=NOW, cashflow=D("0"))
st1 = s2.rule_state(p1, equity=D("10450"), now=NOW)
# Re-confirm the same account/rules one minute later => new profile hash => fresh lineage allowed
p2 = p1.model_copy(update={"user_confirmed_at": NOW - timedelta(minutes=30)})
for i in range(6):
    day = date(2026, 11, 1) + timedelta(days=i)
    snap = BalanceSnapshot(account_id="demo-1", currency="USD", balance="10000", equity="10000",
                           observed_at=datetime.combine(day, datetime.min.time(), PRAGUE))
    s2.record_rule_boundary(p2, snap, evidence_hash="b" * 64, now=NOW, cashflow=D("0"))
st2 = s2.rule_state(p2, equity=D("10450"), now=NOW)
e1 = evaluate_profile(p1, st1, now=NOW)
e2 = evaluate_profile(p2, st2, now=NOW)
out["R2_high_water_lowered_by_reversion"] = {"lineage1_high_water": str(st1.balance_high_water),
    "lineage1_overall_floor": str(e1.overall_floor), "lineage2_high_water": str(st2.balance_high_water),
    "lineage2_overall_floor": str(e2.overall_floor), "same_account": p1.account_id == p2.account_id}

# ---- MT5 adapter: trade-enabled account accepted; raw SDK reachable
from src.trading.mt5_readonly import MT5ReadOnlyAdapter


class FakeSDK:
    ACCOUNT_TRADE_MODE_DEMO = 0
    def __init__(self):
        self.sent = 0
        self.account = SimpleNamespace(login=123, server="FTMO-Demo", currency="USD", trade_mode=0,
                                       trade_allowed=True, trade_expert=True)
        self.terminal = SimpleNamespace(connected=True, trade_allowed=True)
    def initialize(self, *a, **k): return True
    def shutdown(self): pass
    def account_info(self): return self.account
    def terminal_info(self): return self.terminal
    def order_send(self, request): self.sent += 1; return SimpleNamespace(retcode=10009)


sdk = FakeSDK()
ad = MT5ReadOnlyAdapter(sdk=sdk)
ad.connect(expected_login=123, expected_server="FTMO-Demo", expected_currency="USD")
blocked = False
try:
    ad.order_send({})
except PermissionError:
    blocked = True
ad._sdk.order_send({"action": 1, "symbol": "EURUSD", "volume": 1.0})
out["M1_mt5_readonly"] = {"connected_with_trade_allowed_account": ad.health()["connected"],
    "account_trade_allowed": ad.health()["account_trade_allowed"],
    "adapter_method_refused": blocked, "raw_sdk_order_send_calls": sdk.sent}

# ---- Research: SupportResistance on a breakdown bar; caller-asserted VERIFIED_EXTERNAL_FACT evidence
from src.research.multiasset import analyse
inst = Instrument(instrument_id="probe:EURUSD", provider_symbol="EURUSD", asset_class=AssetClass.FOREX,
    product="CFD", venue="PROBE", base_currency="EUR", quote_currency="USD", settlement_currency="USD",
    calendar="probe")
start = NOW - timedelta(hours=60)
bars = []
import math
for i in range(60):
    o = start + timedelta(hours=i)
    c = o + timedelta(hours=1)
    px = D("1.1000") + D(str(round(0.0010 * math.sin(i / 3), 5)))
    close = px
    if i == 59:  # break 30 pips BELOW the 20-bar low
        close = D("1.0950")
    hi = max(px, close) + D("0.0003")
    lo = min(px, close) - D("0.0003")
    bars.append(OHLCVBar(opens_at=o, closes_at=c, open=px, high=hi, low=lo, close=close, volume=None,
        volume_type="UNAVAILABLE", completed=True, adjustment_basis="none",
        provenance=DataProvenance(provider="probe", instrument_id="probe:EURUSD", original_symbol="EURUSD",
            observed_at=c, retrieved_at=c, data_type="ohlcv", freshness="FRESH", quality="VALID",
            permission="RESEARCH")))
fake_fact = ResearchEvidence(evidence_id="ev-1", claim="ECB will cut rates 200bp tomorrow (fabricated)",
    classification="VERIFIED_EXTERNAL_FACT", provenance=bars[-1].provenance)
for fam in ("SupportResistance", "Breakout", "MeanReversion"):
    reg = "TREND" if fam == "Breakout" else "RANGE"
    h = analyse(inst, tuple(bars), now=NOW, family=fam, timeframe="1h", regime=reg, evidence=(fake_fact,))
    out[f"S_{fam}_on_support_break"] = {"entry_thesis": h.entry_thesis, "decision": h.decision_status,
        "supporting_claims": [e.claim for e in h.supporting_evidence],
        "supporting_classes": [e.classification for e in h.supporting_evidence]}
h_regime = analyse(inst, tuple(bars), now=NOW, family="Breakout", timeframe="1h", regime="UNKNOWN")
out["S_regime_unknown_abstains"] = h_regime.decision_status

print(json.dumps(out, indent=1, default=str))
