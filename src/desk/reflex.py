"""
src/desk/reflex.py
==================
THE REFLEX LANE — ARIA's trigger finger. Fires pre-vetted trades in seconds,
not minutes, so a signal that crosses its level gets acted on before the edge
decays. It does NOT replace the deliberate lane (analysts → debate → judge);
it EXECUTES what that lane pre-authorized.

The deliberate lane ARMS a Playbook when a debate lands just under the bar (or
a signal spikes): {ticker, side, trigger_price, stop, target, conviction,
expiry}. The reflex loop watches only armed tickers. On a trigger cross it runs
a fixed, fast pipeline and — if every gate passes — executes via the SAME
order path as the desk (confirmed fill → OCO brackets → track → exact-format
push), logging per-stage latency.

Pipeline (target ≤ 3.5s end to end):
  1. validate    — signal fresh, levels sane            (<50 ms, pure)
  2. risk        — RiskOfficer on the single candidate  (<50 ms)
  3. confidence  — signal probability ≥ reflex_min_prob (<1 ms, pure)
  4. veto        — one Haiku call: PROCEED | VETO       (~1-2 s, FAST tier)
  5. execute     — AutoExecutor._handle (all safety gates + brackets) (~300 ms)

Hard rules unchanged: paper-only contract, RiskOfficer caps, capital
allocator — the LLM can only VETO, never approve beyond the rules. Honors
auto_execute AND a separate reflex_enabled kill switch.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

from src.desk.config import load_config, paper_mode_confirmed
from src.desk.opinion import load_data_json
from src.desk.position_manager import _sane_levels, trading_age_days

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
PLAYBOOKS_FILE = ROOT / "data" / "desk" / "playbooks.json"


# ─────────────────────────────────────────────────────────────────────────────
# Playbook
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Playbook:
    ticker: str
    side: str                 # "buy" | "sell"
    trigger_price: float      # execute when price crosses THROUGH this level
    stop: float | None
    target: float | None
    conviction: int
    debate_id: str
    source: str               # "debate" | "signal"
    armed_at: str
    expires_at: str
    asset_class: str = "equity"
    thesis: str = ""
    status: str = "armed"     # armed | fired | expired

    def to_dict(self) -> dict:
        return asdict(self)


# ── pure trigger / confidence logic (unit-tested) ────────────────────────────

def crossed(side: str, trigger: float, prev_price: float | None,
            price: float) -> bool:
    """True when price has moved THROUGH the trigger in the trade's direction.
    Longs fire on a break UP through the trigger (momentum entry); shorts on a
    break DOWN. With no prior price we fire if already through."""
    if trigger <= 0 or price <= 0:
        return False
    if side in ("buy", "long"):
        if prev_price is None:
            return price >= trigger
        return prev_price < trigger <= price
    else:
        if prev_price is None:
            return price <= trigger
        return prev_price > trigger >= price


def confidence_ok(signal: dict, side: str, min_prob: float) -> tuple[bool, float]:
    """The '95%' gate, honestly implemented: the signal engine's own
    directional probability must clear min_prob. Returns (ok, prob)."""
    if side in ("buy", "long"):
        prob = float(signal.get("bullish_prob") or 0.0)
    else:
        prob = float(signal.get("bearish_prob") or 0.0)
    return prob >= min_prob, prob


def is_expired(pb: dict, now: datetime | None = None) -> bool:
    try:
        return (now or datetime.now()) >= datetime.fromisoformat(pb["expires_at"])
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────────────────────

def load_playbooks() -> list[dict]:
    if PLAYBOOKS_FILE.exists():
        try:
            return json.loads(PLAYBOOKS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"playbooks.json unreadable: {e}")
    return []


def save_playbooks(pbs: list[dict]):
    PLAYBOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAYBOOKS_FILE.write_text(json.dumps(pbs, indent=2, default=str),
                              encoding="utf-8")


def _expiry(days: int) -> str:
    from datetime import timedelta
    d, added = datetime.now(), 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Arming (called by the deliberate lane)
# ─────────────────────────────────────────────────────────────────────────────

def arm_from_debate(transcript: dict, conviction_bar: int,
                    cfg: dict | None = None) -> dict | None:
    """A debate that landed just UNDER the bar with a clean trigger level arms
    a playbook instead of being discarded. Called from the hunt cycle."""
    cfg = cfg or load_config()
    judge = transcript.get("judge") or {}
    verdict = judge.get("verdict")
    conviction = int(judge.get("conviction", 0))
    gap = int(cfg.get("reflex_arm_conviction_gap", 10))
    # Only near-misses: an at/over-bar verdict already trades on the slow lane.
    if verdict not in ("BUY", "SELL"):
        return None
    if not (conviction_bar - gap <= conviction < conviction_bar):
        return None
    ticker = transcript.get("ticker")
    side = "buy" if verdict == "BUY" else "sell"
    sig = load_data_json("signals.json").get(ticker) or {}
    price = float(sig.get("current_price") or 0.0)
    if price <= 0:
        return None
    # Trigger = a small break beyond current price in the trade's direction —
    # the momentum confirmation the debate was waiting on.
    atr_pct = float(sig.get("atr_pct") or 0.01) or 0.01
    trig = price * (1 + 0.5 * atr_pct) if side == "buy" else price * (1 - 0.5 * atr_pct)
    stop = sig.get("stop_loss")
    target = sig.get("take_profit")
    stop, target = _sane_levels(stop, target, price, side == "buy")
    return _arm(ticker, side, trig, stop, target, conviction,
                transcript.get("id", ""), "debate", sig, cfg,
                judge.get("reasoning", "")[:200])


def arm_from_signal(cfg: dict | None = None, account: dict | None = None) -> list[dict]:
    """Intraday composite spikes on liquid, unheld names arm signal playbooks.
    Called on the management tick (cheap: reads cached signals)."""
    cfg = cfg or load_config()
    spike = float(cfg.get("reflex_signal_spike", 40.0))
    held = {p.get("ticker") for p in (account or {}).get("positions") or []}
    armed_now = {p["ticker"] for p in load_playbooks() if p.get("status") == "armed"}
    out = []
    signals = load_data_json("signals.json")
    for ticker, sig in signals.items():
        if not isinstance(sig, dict) or ticker in held or ticker in armed_now:
            continue
        comp = sig.get("composite_score")
        price = float(sig.get("current_price") or 0.0)
        if comp is None or price <= 0:
            continue
        comp = float(comp)
        if abs(comp) < spike:
            continue
        side = "buy" if comp > 0 else "sell"
        if side == "sell" and ticker.endswith("-USD"):
            continue        # can't short crypto on Alpaca
        atr_pct = float(sig.get("atr_pct") or 0.01) or 0.01
        trig = price * (1 + 0.5 * atr_pct) if side == "buy" else price * (1 - 0.5 * atr_pct)
        stop, target = _sane_levels(sig.get("stop_loss"), sig.get("take_profit"),
                                    price, side == "buy")
        pb = _arm(ticker, side, trig, stop, target, int(abs(comp)),
                  "", "signal", sig, cfg,
                  f"composite spike {comp:+.0f}")
        if pb:
            out.append(pb)
    return out


def _arm(ticker, side, trigger, stop, target, conviction, debate_id,
         source, sig, cfg, thesis) -> dict | None:
    days = int(cfg.get("reflex_playbook_expiry_days", 2))
    pb = Playbook(
        ticker=ticker, side=side, trigger_price=round(float(trigger), 4),
        stop=stop, target=target, conviction=conviction, debate_id=debate_id,
        source=source, armed_at=datetime.now().isoformat(),
        expires_at=_expiry(days),
        asset_class="crypto" if str(ticker).endswith("-USD") else "equity",
        thesis=thesis).to_dict()
    pbs = [p for p in load_playbooks()
           if not (p["ticker"] == ticker and p.get("status") == "armed")]
    pbs.append(pb)
    save_playbooks(pbs)
    logger.info(f"REFLEX armed {source} playbook: {side.upper()} {ticker} "
                f"@ trigger {pb['trigger_price']} (conviction {conviction})")
    return pb


# ─────────────────────────────────────────────────────────────────────────────
# The reflex engine
# ─────────────────────────────────────────────────────────────────────────────

class ReflexEngine:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self._last_price: dict = {}
        self._running = False
        self._thread = None

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if self._running or not self.cfg.get("reflex_enabled", True):
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("REFLEX engine started (poll every "
                    f"{self.cfg.get('reflex_poll_seconds', 3)}s)")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self.scan_once()
            except Exception:
                logger.exception("reflex scan failed")
            time.sleep(max(1, int(self.cfg.get("reflex_poll_seconds", 3))))

    # ── one scan pass (also the manual/tested entry point) ───────────────

    def scan_once(self) -> list[dict]:
        self.cfg = load_config()      # pick up kill-switch / param changes
        if not self.cfg.get("reflex_enabled", True):
            return []
        pbs = load_playbooks()
        armed = [p for p in pbs if p.get("status") == "armed"]
        if not armed:
            return []

        from src.desk.auto_executor import account_snapshot, get_order_manager
        mgr = get_order_manager()
        if mgr is None or mgr._alpaca is None or not mgr._alpaca.is_connected():
            return []

        # Equities only fire while the US market can fill; crypto 24/7.
        from src.desk.desk_daemon import us_equities_open
        equities_open = us_equities_open()
        results, changed = [], False
        for pb in armed:
            if is_expired(pb):
                pb["status"] = "expired"
                changed = True
                continue
            if pb["asset_class"] != "crypto" and not equities_open:
                continue
            price = self._quote(pb["ticker"], mgr)
            if price <= 0:
                continue
            prev = self._last_price.get(pb["ticker"])
            self._last_price[pb["ticker"]] = price
            if not crossed(pb["side"], pb["trigger_price"], prev, price):
                continue
            outcome = self._fire(pb, price, account_snapshot(), mgr)
            results.append(outcome)
            if outcome.get("executed") or outcome.get("terminal"):
                pb["status"] = "fired" if outcome.get("executed") else "expired"
                changed = True
        if changed:
            save_playbooks(pbs)
        return results

    # ── the fast pipeline for one triggered playbook ─────────────────────

    def _fire(self, pb: dict, price: float, account: dict, mgr) -> dict:
        t0 = time.monotonic()
        lat = {}
        ticker = pb["ticker"]
        sig = load_data_json("signals.json").get(ticker) or {}

        # 1. validate — fresh signal + sane levels
        stop, target = _sane_levels(pb.get("stop"), pb.get("target"),
                                    price, pb["side"] == "buy")
        lat["validate"] = _ms(t0)
        if not sig.get("current_price"):
            return {**self._base(pb, price), "stage": "validate",
                    "reason": "no signal data", "executed": False}

        # 3. confidence gate (cheap; do before the paid veto)
        ok, prob = confidence_ok(sig, pb["side"], float(self.cfg["reflex_min_prob"]))
        if not ok:
            return {**self._base(pb, price), "stage": "confidence",
                    "reason": f"signal prob {prob:.2f} < {self.cfg['reflex_min_prob']}",
                    "executed": False, "latency_ms": lat}

        # 2. risk — size + RiskOfficer + capital allocator on one candidate
        t1 = time.monotonic()
        entry = self._size(pb, price, sig, account)
        if entry is None:
            return {**self._base(pb, price), "stage": "size",
                    "reason": "sizing produced zero qty", "executed": False,
                    "latency_ms": lat}
        approved = self._risk_check(entry, account)
        lat["risk"] = _ms(t1)
        if not approved:
            return {**self._base(pb, price), "stage": "risk",
                    "reason": entry.get("_reject", "risk officer rejected"),
                    "executed": False, "latency_ms": lat}

        # 4. veto — one fast LLM sanity pass (VETO only, never approves)
        t2 = time.monotonic()
        veto = self._veto(entry, price, sig)
        lat["veto"] = _ms(t2)
        if veto is not None and veto.get("veto"):
            return {**self._base(pb, price), "stage": "veto",
                    "reason": f"LLM veto: {veto.get('reason', '')}",
                    "executed": False, "latency_ms": lat}

        # 5. execute — reuse the desk's fully-gated single-entry path
        t3 = time.monotonic()
        from src.desk.auto_executor import AutoExecutor
        transcript = {"id": pb.get("debate_id", ""), "judge": {},
                      "rounds": []}
        record = AutoExecutor(self.cfg)._handle(entry, transcript)
        lat["submit"] = _ms(t3)
        lat["total"] = _ms(t0)
        executed = record.get("mode") == "auto"
        out = {**self._base(pb, price), "stage": "execute",
               "reason": record.get("reason", record.get("mode")),
               "executed": executed, "mode": record.get("mode"),
               "fill_price": record.get("fill_price"), "latency_ms": lat}
        logger.info(f"REFLEX {'FIRED' if executed else 'stopped at exec'} "
                    f"{pb['side'].upper()} {ticker} — total {lat['total']}ms "
                    f"({record.get('mode')})")
        return out

    # ── stage helpers ────────────────────────────────────────────────────

    def _size(self, pb, price, sig, account) -> dict | None:
        from src.desk.capital_allocator import allocate_for_account
        regime = sig.get("regime") or ""
        alloc = allocate_for_account(account, regime, self.cfg)
        equity = float(account.get("equity") or 0.0) or 100_000.0
        risk_amt = equity * alloc.risk_per_trade_pct / 100.0 * alloc.size_multiplier
        stop = pb.get("stop") or (price * 0.97 if pb["side"] == "buy" else price * 1.03)
        per_share_risk = abs(price - stop) or (price * 0.02)
        qty = risk_amt / per_share_risk
        if pb["asset_class"] == "crypto":
            qty = round(qty, 6)
            if qty < 0.0001:
                return None
        else:
            qty = float(int(qty))
            if qty < 1:
                return None
        notional = round(qty * price, 2)
        return {
            "ticker": pb["ticker"], "side": pb["side"], "qty": qty,
            "price": price, "notional": notional,
            "risk_amount": round(per_share_risk * qty, 2),
            "conviction": pb["conviction"], "thesis": pb.get("thesis", ""),
            "stop": pb.get("stop"), "target": pb.get("target"),
            "invalidation": sig.get("invalidation"),
            "sector": None, "asset_class": pb["asset_class"],
            "signal_score": sig.get("composite_score", 0.0),
            "debate_id": pb.get("debate_id", ""),
            "regime": sig.get("regime", ""), "source": f"reflex:{pb['source']}",
            "_alloc": alloc,
        }

    def _risk_check(self, entry, account) -> bool:
        try:
            from src.desk.analysts import macro_agent
            conditioner = macro_agent.condition()
        except Exception as e:
            # FAIL CLOSED. This used to `return True`, which reads as "approved"
            # and skipped every cap the RiskOfficer owns — name %, sector %,
            # portfolio heat, correlation, position cap, conviction bar and the
            # drawdown circuit-breaker — because the officer is not constructed
            # until the line below. The old comment claimed the gates re-run at
            # execution; they do not. auto_executor.gates() checks
            # paper/armed/connected/budget and never a risk cap, so this was the
            # only place they were enforced on the reflex lane.
            #
            # No attacker needed: a string where a number belongs in
            # data/macro_data.json raises TypeError inside condition(), and this
            # lane runs every three seconds from server startup. The deliberate
            # lane calls the same condition() outside any try (desk_daemon.py),
            # so the identical fault already fails that lane closed — two lanes,
            # one fault, opposite outcomes was the tell that this was a bug.
            logger.warning(f"reflex risk check could not condition ({e}) — "
                           f"rejecting {entry.get('ticker', '?')}")
            entry["_reject"] = f"risk officer unavailable: {e}"
            return False
        from src.desk.risk_officer import RiskOfficer
        officer = RiskOfficer(self.cfg, conditioner)
        officer.allocation = entry.get("_alloc")
        approved, rejected, _ = officer.review([{k: v for k, v in entry.items()
                                                 if not k.startswith("_")}], account)
        if approved:
            # carry any risk-officer trim back onto the entry
            entry.update({"qty": approved[0]["qty"],
                          "notional": approved[0]["notional"]})
            return True
        entry["_reject"] = rejected[0]["reason"] if rejected else "risk rejected"
        return False

    def _veto(self, entry, price, sig) -> dict | None:
        """One FAST-tier call. Returns {'veto': bool, 'reason'} or None if the
        LLM was unreachable (then reflex_on_llm_fail decides)."""
        try:
            from src.inference.router import Tier, get_router
            prompt = (
                f"Trade check. {entry['side'].upper()} {entry['ticker']} at "
                f"{price:.2f}, stop {entry.get('stop')}, target {entry.get('target')}, "
                f"signal composite {sig.get('composite_score')}, "
                f"regime {sig.get('regime')}. Reply exactly PROCEED, or "
                f"VETO:<=6-word-reason if this is an obviously bad trade "
                f"(e.g. earnings imminent, price already past target, halted).")
            r = get_router().complete(
                Tier.FAST, [{"role": "user", "content": prompt}],
                max_tokens=int(self.cfg.get("reflex_veto_max_tokens", 30)),
                temperature=0.0, timeout=3)
            txt = (r.text or "").strip().upper()
            if txt.startswith("VETO"):
                return {"veto": True, "reason": r.text.split(":", 1)[-1][:60]}
            return {"veto": False, "reason": ""}
        except Exception as e:
            logger.warning(f"reflex veto unreachable ({e}) — "
                           f"fail-safe {self.cfg.get('reflex_on_llm_fail', 'skip')}")
            if self.cfg.get("reflex_on_llm_fail", "skip") == "proceed":
                return {"veto": False, "reason": "llm-unreachable-proceed"}
            return {"veto": True, "reason": "llm-unreachable-skip"}

    def _quote(self, ticker, mgr) -> float:
        try:
            q = mgr._alpaca.get_quote(ticker)
            return float(q.get("mid") or 0.0)
        except Exception:
            sig = load_data_json("signals.json").get(ticker) or {}
            return float(sig.get("current_price") or 0.0)

    def _base(self, pb, price) -> dict:
        return {"at": datetime.now().isoformat(), "ticker": pb["ticker"],
                "side": pb["side"], "trigger": pb["trigger_price"],
                "price": price, "source": pb["source"]}


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


_reflex: ReflexEngine | None = None


def get_reflex() -> ReflexEngine:
    global _reflex
    if _reflex is None:
        _reflex = ReflexEngine()
    return _reflex
