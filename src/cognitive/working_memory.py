"""
working_memory.py — ARIA Working Memory
Reads current TIS state and formats it for injection into the Claude system prompt.
"""

import json
import os
from datetime import datetime
from pathlib import Path


class WorkingMemory:
    """Snapshot of the current TIS run, refreshed each conversation turn."""

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)
        self._signals: dict = {}
        self._trade_candidates: dict = {}
        self._watchlist: dict = {}
        self._political_signals: dict = {}
        self._log_tail: str = ""
        self._loaded_at: datetime = None

    # ── I/O helpers ──────────────────────────────────────────────────────────

    def refresh(self):
        """Reload all TIS data from disk."""
        self._signals = self._load_json("data/signals.json")
        self._trade_candidates = self._load_json("data/trade_candidates.json")
        self._watchlist = self._load_json("data/political/watchlist.json")
        self._political_signals = self._load_json("data/political/political_signals.json")
        self._log_tail = self._parse_log()
        self._loaded_at = datetime.now()

    def _load_json(self, relative_path: str) -> dict:
        path = self.base_path / relative_path
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _parse_log(self) -> str:
        log_path = self.base_path / "tis_run.log"
        if not log_path.exists():
            return ""
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            relevant = [l.rstrip() for l in lines[-60:] if l.strip()]
            return "\n".join(relevant)
        except Exception:
            return ""

    # ── Accessors ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ticker_score(d: dict):
        """Tolerate different score field names across TIS schema versions."""
        for key in ("score", "composite_score", "signal_score", "portfolio_score"):
            if d.get(key) is not None:
                return d[key]
        return 0

    def top_longs(self, n: int = 10) -> list:
        longs = [
            (t, d) for t, d in self._signals.items()
            if t != "__meta__" and isinstance(d, dict) and d.get("direction") == "long"
        ]
        longs.sort(key=lambda x: self._ticker_score(x[1]) or 0, reverse=True)
        return longs[:n]

    def top_shorts(self, n: int = 10) -> list:
        shorts = [
            (t, d) for t, d in self._signals.items()
            if t != "__meta__" and isinstance(d, dict) and d.get("direction") == "short"
        ]
        shorts.sort(key=lambda x: self._ticker_score(x[1]) or 0)
        return shorts[:n]

    def get_ticker_context(self, ticker: str) -> dict:
        ticker = ticker.upper()
        return {
            "ticker": ticker,
            "signal": self._signals.get(ticker, {}),
            "political": self._watchlist.get(ticker, {}),
            "political_signal": self._political_signals.get(ticker, {}),
            "trade_candidate": self._find_trade_candidate(ticker),
        }

    def _find_trade_candidate(self, ticker: str) -> dict:
        if not isinstance(self._trade_candidates, dict):
            return {}
        for category, items in self._trade_candidates.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("ticker") == ticker:
                        return {"category": category, **item}
        return {}

    def get_meta(self) -> dict:
        return self._signals.get("__meta__", {})

    def _meta_lookup(self, meta: dict, candidates: list):
        """Try each candidate key name (case-insensitive) and return (value, matched_key) or (None, None)."""
        if not meta:
            return None, None
        lower_map = {k.lower(): k for k in meta.keys()}
        for candidate in candidates:
            actual_key = lower_map.get(candidate.lower())
            if actual_key is not None and meta.get(actual_key) is not None:
                return meta[actual_key], actual_key
        return None, None

    def all_signals(self) -> dict:
        return {k: v for k, v in self._signals.items() if k != "__meta__"}

    def political_flags(self) -> dict:
        return {
            t: d for t, d in self._watchlist.items()
            if isinstance(d, dict) and d.get("flag")
        }

    # ── Context string ────────────────────────────────────────────────────────

    def to_context(self) -> str:
        if not self._loaded_at:
            return "[Working memory not loaded — call refresh() first]"

        meta = self.get_meta()
        lines = [
            f"=== CURRENT TIS STATE  (as of {self._loaded_at.strftime('%Y-%m-%d %H:%M')}) ===",
        ]

        # Portfolio overview
        all_sig = self.all_signals()
        n_longs = sum(1 for d in all_sig.values() if isinstance(d, dict) and d.get("direction") == "long")
        n_shorts = sum(1 for d in all_sig.values() if isinstance(d, dict) and d.get("direction") == "short")
        top_l = self.top_longs(1)
        top_s = self.top_shorts(1)

        lines.append("\nPORTFOLIO OVERVIEW:")
        lines.append(f"  Universe : {len(all_sig)} tickers tracked")
        lines.append(f"  Signals  : {n_longs} longs / {n_shorts} shorts")

        # Try known field-name aliases first (different TIS versions/configs use different keys)
        used_keys = set()
        score_val, score_key = self._meta_lookup(meta, ["combined_score", "portfolio_score", "score", "composite_score"])
        regime_val, regime_key = self._meta_lookup(meta, ["regime", "macro_regime", "market_regime"])
        var_val, var_key = self._meta_lookup(meta, ["var_1d", "var", "value_at_risk", "var_1d_usd", "portfolio_var"])
        cvar_val, cvar_key = self._meta_lookup(meta, ["cvar", "cvar_1d", "conditional_var"])
        sharpe_val, sharpe_key = self._meta_lookup(meta, ["sharpe", "sharpe_ratio", "est_sharpe"])

        if score_val is not None:
            lines.append(f"  Score    : {score_val}")
            used_keys.add(score_key)
        if regime_val:
            lines.append(f"  Regime   : {regime_val}")
            used_keys.add(regime_key)
        if var_val is not None:
            lines.append(f"  VaR 1d   : ${var_val:,.0f}" if isinstance(var_val, (int, float)) else f"  VaR 1d   : {var_val}")
            used_keys.add(var_key)
        if cvar_val is not None:
            lines.append(f"  CVaR     : ${cvar_val:,.0f}" if isinstance(cvar_val, (int, float)) else f"  CVaR     : {cvar_val}")
            used_keys.add(cvar_key)
        if sharpe_val is not None:
            lines.append(f"  Sharpe   : {sharpe_val}")
            used_keys.add(sharpe_key)

        # Fallback: if none of the known aliases matched but __meta__ has data,
        # dump whatever fields actually exist rather than silently showing nothing.
        if meta and not used_keys:
            lines.append("  (Unrecognized __meta__ schema — showing raw fields:)")
            for k, v in meta.items():
                if k != "strategies" and not isinstance(v, (dict, list)):
                    lines.append(f"    {k}: {v}")
        elif meta:
            leftover = {k: v for k, v in meta.items() if k not in used_keys and k != "strategies" and not isinstance(v, (dict, list))}
            if leftover:
                lines.append("  Other meta fields:")
                for k, v in leftover.items():
                    lines.append(f"    {k}: {v}")

        if top_l:
            lines.append(f"  Top Long : {top_l[0][0]} (score={self._ticker_score(top_l[0][1])})")
        if top_s:
            lines.append(f"  Top Short: {top_s[0][0]} (score={self._ticker_score(top_s[0][1])})")

        # Top longs
        longs = self.top_longs(10)
        if longs:
            lines.append("\nTOP LONGS:")
            for ticker, d in longs:
                ml = d.get("ml_prediction", "?")
                ml_conf = d.get("ml_confidence", "")
                conf_str = f" [{ml_conf:.0%}]" if isinstance(ml_conf, float) else ""
                lines.append(f"  {ticker:<6} score={self._ticker_score(d):<6} ml={ml}{conf_str}")

        # Top shorts
        shorts = self.top_shorts(10)
        if shorts:
            lines.append("\nTOP SHORTS:")
            for ticker, d in shorts:
                ml = d.get("ml_prediction", "?")
                lines.append(f"  {ticker:<6} score={self._ticker_score(d):<6} ml={ml}")

        # Political flags
        flags = self.political_flags()
        if flags:
            lines.append(f"\nPOLITICAL FLAGS ({len(flags)} tickers):")
            for ticker, d in list(flags.items())[:8]:
                lines.append(f"  {ticker}: {d.get('reason', d.get('flag', 'flagged'))}")

        # Strategy outputs from meta
        if meta.get("strategies"):
            lines.append("\nSTRATEGY OUTPUTS:")
            for strat, result in meta["strategies"].items():
                lines.append(f"  {strat}: {result}")

        # Log tail
        if self._log_tail:
            tail_lines = self._log_tail.split("\n")[-12:]
            lines.append("\nLAST RUN (tail):")
            for line in tail_lines:
                lines.append(f"  {line}")

        return "\n".join(lines)
