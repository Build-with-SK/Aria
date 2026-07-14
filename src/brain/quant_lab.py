"""
src/brain/quant_lab.py
======================
SELF-LEARNING QUANT RESEARCHER — fully automated, zero-attention loop.

Every cycle (default: every 12 hours), with no human in the loop:

  1. READ    — pull the newest quantitative-finance papers from arXiv
               (q-fin.TR / q-fin.PM / q-fin.ST, official API) and index
               them into ChromaDB (collection `quant_research`) next to
               the brain's other memories.
  2. THINK   — map each new paper onto a parameterized strategy template.
               If Ollama is running, the local LLM reads the abstract and
               picks the template + parameters (JSON out); otherwise a
               deterministic keyword mapper does it. Either way the paper
               is cited on the strategy card.
  3. TEST    — vectorized backtest on a liquid NSE basket (3y daily bars,
               0.1%/side costs). Full-period AND last-1y out-of-sample
               metrics: CAGR, Sharpe, max drawdown.
  4. RECORD  — rank the library by OOS Sharpe, persist to
               data/quant_lab/strategies.json, and write a markdown
               report into the Obsidian vault (TIS folder).

NOTHING HERE TRADES. The lab produces research; execution stays behind
the human-approval queue (see CLAUDE.md: the brain proposes, the human
approves). Backtests are simulations of the past, not promises.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LAB_DIR = ROOT / "data" / "quant_lab"
STATE_FILE = LAB_DIR / "state.json"
STRATEGIES_FILE = LAB_DIR / "strategies.json"
PAPERS_FILE = LAB_DIR / "papers.json"
PRICES_CACHE = LAB_DIR / "prices_cache.pkl"
CHROMA_PATH = ROOT / "data" / "brain_memory" / "chromadb"

ARXIV_URL = (
    "http://export.arxiv.org/api/query?search_query="
    "cat:q-fin.TR+OR+cat:q-fin.PM+OR+cat:q-fin.ST"
    "&sortBy=submittedDate&sortOrder=descending&max_results=20"
)

# Liquid NSE research basket (large caps across sectors)
BASKET = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL",
    "ITC", "LT", "KOTAKBANK", "AXISBANK", "MARUTI", "SUNPHARMA", "TITAN",
    "ULTRACEMCO", "NTPC", "POWERGRID", "TATASTEEL", "WIPRO", "HINDUNILVR",
]
COST_PER_SIDE = 0.001  # 0.1% transaction cost
OOS_DAYS = 252         # last year held out for out-of-sample stats

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "momentum_topn":   {"lookback": 126, "top_n": 5},
    "ma_cross":        {"fast": 20, "slow": 100},
    "mean_reversion_z": {"window": 20, "entry_z": -2.0, "exit_z": 0.0},
    "rsi_reversal":    {"period": 14, "buy_below": 30, "sell_above": 70},
    "breakout":        {"window": 55, "exit_ma": 20},
}

KEYWORD_MAP = [
    (("momentum", "trend", "time-series momentum", "cross-sectional"), "momentum_topn"),
    (("mean rever", "reversal", "ornstein", "stationar", "pairs"), "mean_reversion_z"),
    (("breakout", "channel", "turtle", "range"), "breakout"),
    (("rsi", "oscillator", "overbought", "oversold"), "rsi_reversal"),
    (("moving average", "crossover", "sma", "ema"), "ma_cross"),
]


# ═══════════════════════════════════════════════════════════════════════════
# Backtest engine — vectorized, long-only, costs included
# ═══════════════════════════════════════════════════════════════════════════
def _positions(template: str, params: Dict, closes) -> "Any":
    """Return a 0/1 position DataFrame aligned to closes."""
    import pandas as pd

    if template == "momentum_topn":
        look, n = int(params["lookback"]), int(params["top_n"])
        mom = closes.pct_change(look)
        monthly = mom.resample("ME").last()
        picks = monthly.rank(axis=1, ascending=False) <= n
        pos = picks.reindex(closes.index, method="ffill").fillna(False)
        return pos.astype(float)

    if template == "ma_cross":
        fast = closes.rolling(int(params["fast"])).mean()
        slow = closes.rolling(int(params["slow"])).mean()
        return (fast > slow).astype(float)

    if template == "mean_reversion_z":
        w = int(params["window"])
        z = (closes - closes.rolling(w).mean()) / closes.rolling(w).std()
        entry, exit_ = float(params["entry_z"]), float(params["exit_z"])
        pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        state = {c: 0.0 for c in closes.columns}
        zv = z.values
        for i in range(len(closes)):
            for j, c in enumerate(closes.columns):
                v = zv[i, j]
                if v == v:  # not NaN
                    if state[c] == 0.0 and v <= entry:
                        state[c] = 1.0
                    elif state[c] == 1.0 and v >= exit_:
                        state[c] = 0.0
                pos.iat[i, j] = state[c]
        return pos

    if template == "rsi_reversal":
        p = int(params["period"])
        delta = closes.diff()
        up = delta.clip(lower=0).rolling(p).mean()
        dn = (-delta.clip(upper=0)).rolling(p).mean()
        rsi = 100 - 100 / (1 + up / dn)
        buy, sell = float(params["buy_below"]), float(params["sell_above"])
        pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        state = {c: 0.0 for c in closes.columns}
        rv = rsi.values
        for i in range(len(closes)):
            for j, c in enumerate(closes.columns):
                v = rv[i, j]
                if v == v:
                    if state[c] == 0.0 and v <= buy:
                        state[c] = 1.0
                    elif state[c] == 1.0 and v >= sell:
                        state[c] = 0.0
                pos.iat[i, j] = state[c]
        return pos

    if template == "breakout":
        w, ema = int(params["window"]), int(params["exit_ma"])
        hi = closes.rolling(w).max().shift(1)
        ma = closes.rolling(ema).mean()
        entered = closes >= hi
        holding = closes > ma
        return (entered | holding).astype(float) * (closes > ma).astype(float)

    raise ValueError(f"unknown template {template}")


def _metrics(rets) -> Dict[str, Any]:
    import numpy as np
    eq = (1 + rets).cumprod()
    n = len(rets)
    if n < 40 or eq.iloc[-1] <= 0:
        return {"cagr_pct": None, "sharpe": None, "max_dd_pct": None, "days": n}
    cagr = eq.iloc[-1] ** (252 / n) - 1
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    return {
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(dd * 100, 2),
        "days": n,
    }


def backtest(template: str, params: Dict, closes) -> Dict[str, Any]:
    pos = _positions(template, params, closes)
    active = pos.sum(axis=1)
    weights = pos.div(active.replace(0, 1), axis=0)          # equal weight across active
    daily = closes.pct_change().fillna(0.0)
    gross = (weights.shift(1).fillna(0.0) * daily).sum(axis=1)
    turnover = (weights - weights.shift(1)).abs().sum(axis=1).fillna(0.0)
    net = gross - turnover * COST_PER_SIDE
    eq = (1 + net).cumprod()
    monthly_eq = eq.resample("ME").last().dropna()
    return {
        "full": _metrics(net),
        "oos": _metrics(net.tail(OOS_DAYS)),
        "equity_curve": [[str(k.date()), round(float(v), 4)] for k, v in monthly_eq.items()],
        "exposure_pct": round(float((active > 0).mean()) * 100, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════
# The lab
# ═══════════════════════════════════════════════════════════════════════════
class QuantLab:
    def __init__(self) -> None:
        LAB_DIR.mkdir(parents=True, exist_ok=True)
        self.scheduler = None
        self.running = False
        self.working = False
        self.interval_hours = 12
        self._cycle_lock = threading.Lock()
        self.state = self._load_json(STATE_FILE, {"runs": 0, "last_run": None, "last_summary": {}})

    # ── persistence helpers ──────────────────────────────────────────────
    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _save_json(path: Path, obj) -> None:
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    # ── lifecycle (mirrors BrainDaemon) ──────────────────────────────────
    def start(self) -> None:
        if self.running:
            return
        from apscheduler.schedulers.background import BackgroundScheduler
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(self.run_cycle, "interval", hours=self.interval_hours,
                               id="quant_lab_cycle", replace_existing=True)
        self.scheduler.start()
        self.running = True
        self.scheduler.add_job(self.run_cycle, "date")   # first cycle immediately
        logger.info(f"Quant lab started (every {self.interval_hours}h)")

    def stop(self) -> None:
        self.running = False
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            self.scheduler = None

    def run_now(self) -> None:
        threading.Thread(target=self.run_cycle, daemon=True).start()

    def status(self) -> Dict[str, Any]:
        papers = self._load_json(PAPERS_FILE, [])
        strats = self._load_json(STRATEGIES_FILE, [])
        return {
            "running": self.running,
            "working": self.working,
            "interval_hours": self.interval_hours,
            "runs": self.state.get("runs", 0),
            "last_run": self.state.get("last_run"),
            "last_summary": self.state.get("last_summary", {}),
            "papers_ingested": len(papers),
            "strategies_tested": len(strats),
            "note": "Research only — execution stays behind the human-approval queue.",
        }

    # ── step 1: read papers ──────────────────────────────────────────────
    def _fetch_arxiv(self) -> List[Dict[str, Any]]:
        req = urllib.request.Request(ARXIV_URL, headers={"User-Agent": "ARIA-quant-lab/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for entry in ET.fromstring(xml_text).findall("a:entry", ns):
            pid = (entry.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
            out.append({
                "id": pid,
                "title": " ".join((entry.findtext("a:title", "", ns) or "").split()),
                "summary": " ".join((entry.findtext("a:summary", "", ns) or "").split()),
                "published": entry.findtext("a:published", "", ns),
                "link": f"https://arxiv.org/abs/{pid}",
                "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)][:5],
            })
        return out

    def _index_papers(self, papers: List[Dict[str, Any]]) -> int:
        """Embed new papers into ChromaDB so chat/brain can recall them."""
        if not papers:
            return 0
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
            client = chromadb.PersistentClient(path=str(CHROMA_PATH))
            coll = client.get_or_create_collection("quant_research")
            embedder = SentenceTransformer("all-MiniLM-L6-v2")
            docs = [f"{p['title']}\n\n{p['summary']}" for p in papers]
            coll.upsert(
                ids=[p["id"] for p in papers],
                documents=docs,
                embeddings=embedder.encode(docs).tolist(),
                metadatas=[{"title": p["title"], "link": p["link"],
                            "published": p["published"] or ""} for p in papers],
            )
            return len(papers)
        except Exception as e:
            logger.warning(f"quant paper embedding skipped: {e}")
            return 0

    # ── step 2: think (LLM if available, deterministic fallback) ────────
    def _ollama_map(self, paper: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            prompt = (
                "You map a finance paper to ONE backtestable template.\n"
                f"Templates and default params: {json.dumps(TEMPLATES)}\n"
                f"PAPER TITLE: {paper['title']}\nABSTRACT: {paper['summary'][:1200]}\n"
                'Reply ONLY JSON: {"template": "<name>", "params": {...}, "rationale": "<one sentence>"}'
            )
            body = json.dumps({"model": "qwen2.5-coder:7b", "prompt": prompt,
                               "stream": False, "format": "json",
                               "options": {"temperature": 0.2}}).encode()
            req = urllib.request.Request("http://localhost:11434/api/generate",
                                         data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = json.loads(resp.read().decode())["response"]
            spec = json.loads(raw)
            if spec.get("template") in TEMPLATES:
                params = {**TEMPLATES[spec["template"]], **(spec.get("params") or {})}
                return {"template": spec["template"], "params": params,
                        "rationale": spec.get("rationale", ""), "mapper": "ollama"}
        except Exception as e:
            logger.debug(f"ollama mapping unavailable: {e}")
        return None

    @staticmethod
    def _keyword_map(paper: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text = f"{paper['title']} {paper['summary']}".lower()
        for keys, template in KEYWORD_MAP:
            if any(k in text for k in keys):
                return {"template": template, "params": dict(TEMPLATES[template]),
                        "rationale": f"abstract mentions {[k for k in keys if k in text][0]!r}",
                        "mapper": "keywords"}
        return None

    # ── step 3: test ─────────────────────────────────────────────────────
    def _get_closes(self):
        """3y daily closes for the basket, cached for 24h."""
        import pandas as pd
        if PRICES_CACHE.exists():
            age_h = (datetime.now().timestamp() - PRICES_CACHE.stat().st_mtime) / 3600
            if age_h < 24:
                try:
                    return pd.read_pickle(PRICES_CACHE)
                except Exception:
                    pass
        import yfinance as yf
        raw = yf.download([f"{s}.NS" for s in BASKET], period="3y", interval="1d",
                          progress=False, threads=True, group_by="ticker")
        closes = pd.DataFrame({
            s: raw[f"{s}.NS"]["Close"]
            for s in BASKET if f"{s}.NS" in raw.columns.get_level_values(0)
        }).dropna(how="all").ffill()
        closes.to_pickle(PRICES_CACHE)
        return closes

    # ── the full cycle ───────────────────────────────────────────────────
    def run_cycle(self) -> None:
        if not self._cycle_lock.acquire(blocking=False):
            logger.info("Quant lab cycle skipped — previous still running")
            return
        self.working = True
        summary = {"started": datetime.now().isoformat(), "new_papers": 0,
                   "new_strategies": 0, "errors": []}
        try:
            known = self._load_json(PAPERS_FILE, [])
            known_ids = {p["id"] for p in known}
            strategies = self._load_json(STRATEGIES_FILE, [])
            tested_papers = {s["paper"]["id"] for s in strategies if s.get("paper")}

            # 1 — READ
            try:
                fresh = [p for p in self._fetch_arxiv() if p["id"] not in known_ids]
                summary["new_papers"] = len(fresh)
                if fresh:
                    known = fresh + known
                    self._save_json(PAPERS_FILE, known[:400])
                    self._index_papers(fresh)
            except Exception as e:
                summary["errors"].append(f"arxiv: {e}")
                fresh = []

            # 2+3 — THINK & TEST (also catch up on papers never tested)
            closes = None
            seen_specs = {json.dumps({"t": s["template"], "p": s["params"]}, sort_keys=True)
                          for s in strategies}
            todo = [p for p in known if p["id"] not in tested_papers][:8]
            for paper in todo:
                spec = self._ollama_map(paper) or self._keyword_map(paper)
                if spec is None:
                    tested_papers.add(paper["id"])  # nothing mappable — skip forever
                    continue
                spec_key = json.dumps({"t": spec["template"], "p": spec["params"]}, sort_keys=True)
                if spec_key in seen_specs:
                    # identical template+params already in the library — cite the
                    # extra paper on the existing entry instead of duplicating
                    for s in strategies:
                        if (s["template"] == spec["template"] and s["params"] == spec["params"]
                                and paper["id"] not in [x["id"] for x in s.get("also_cited", [])]):
                            s.setdefault("also_cited", []).append(
                                {"id": paper["id"], "title": paper["title"], "link": paper["link"]})
                            break
                    tested_papers.add(paper["id"])
                    continue
                seen_specs.add(spec_key)
                try:
                    if closes is None:
                        closes = self._get_closes()
                    result = backtest(spec["template"], spec["params"], closes)
                    strategies.append({
                        "id": f"{spec['template']}-{paper['id']}",
                        "name": f"{spec['template']} · {paper['title'][:60]}",
                        "template": spec["template"],
                        "params": spec["params"],
                        "mapper": spec["mapper"],
                        "rationale": spec["rationale"],
                        "paper": {"id": paper["id"], "title": paper["title"], "link": paper["link"]},
                        "metrics": {"full": result["full"], "oos": result["oos"]},
                        "exposure_pct": result["exposure_pct"],
                        "equity_curve": result["equity_curve"],
                        "basket": "NSE large-cap 20",
                        "created_at": datetime.now().isoformat(),
                    })
                    summary["new_strategies"] += 1
                except Exception as e:
                    summary["errors"].append(f"backtest {paper['id']}: {e}")
                tested_papers.add(paper["id"])

            # 4 — RECORD
            strategies.sort(key=lambda s: (s["metrics"]["oos"]["sharpe"] is not None,
                                           s["metrics"]["oos"]["sharpe"] or -99), reverse=True)
            self._save_json(STRATEGIES_FILE, strategies[:200])
            self._write_report(strategies)

            self.state["runs"] = self.state.get("runs", 0) + 1
            self.state["last_run"] = datetime.now().isoformat()
            summary["finished"] = datetime.now().isoformat()
            self.state["last_summary"] = summary
            self._save_json(STATE_FILE, self.state)
            logger.info(f"Quant lab cycle done: {summary}")
        finally:
            self.working = False
            self._cycle_lock.release()

    # ── step 4: report into the vault ───────────────────────────────────
    def _write_report(self, strategies: List[Dict[str, Any]]) -> None:
        lines = [
            "# Quant Lab Report", "",
            f"_Auto-generated {datetime.now():%Y-%m-%d %H:%M} — research only, "
            "not investment advice. Backtests are simulations of the past._", "",
            f"Strategies tested: **{len(strategies)}** · basket: NSE large-cap 20 · "
            f"costs {COST_PER_SIDE*100:.1f}%/side · OOS = last {OOS_DAYS} trading days", "",
            "| # | Strategy | OOS Sharpe | OOS CAGR | OOS MaxDD | Full Sharpe | Paper |",
            "|---|----------|-----------:|---------:|----------:|------------:|-------|",
        ]
        for i, s in enumerate(strategies[:15], 1):
            oos, full = s["metrics"]["oos"], s["metrics"]["full"]
            lines.append(
                f"| {i} | {s['template']} | {oos['sharpe']} | {oos['cagr_pct']}% "
                f"| {oos['max_dd_pct']}% | {full['sharpe']} "
                f"| [{s['paper']['title'][:40]}…]({s['paper']['link']}) |"
            )
        report = "\n".join(lines) + "\n"
        (LAB_DIR / "report.md").write_text(report, encoding="utf-8")
        try:
            import yaml
            cfg = yaml.safe_load((ROOT / "configs" / "obsidian.yaml").read_text(encoding="utf-8"))
            tis = Path(cfg["obsidian"]["tis_root"])
            if tis.exists():
                (tis / "Quant Lab Report.md").write_text(report, encoding="utf-8")
        except Exception as e:
            logger.debug(f"vault report skipped: {e}")

    def papers(self, n: int = 50) -> List[Dict[str, Any]]:
        return self._load_json(PAPERS_FILE, [])[:n]

    def strategies(self, n: int = 50) -> List[Dict[str, Any]]:
        return self._load_json(STRATEGIES_FILE, [])[:n]


# ---------------------------------------------------------------------------
_lab: Optional[QuantLab] = None
_lab_lock = threading.Lock()


def get_lab() -> QuantLab:
    global _lab
    if _lab is None:
        with _lab_lock:
            if _lab is None:
                _lab = QuantLab()
    return _lab
