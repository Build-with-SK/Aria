"""
src/v5/report.py
================
Investment-committee rendering (spec §12).

Precise numbers, sourced claims, explicit assumptions, a confidence range, and a
clear invalidation condition. The dissenting view is printed in full — a losing
argument is outweighed here, never deleted.

The renderer is deterministic: the same analysis dict always produces the same
document. No LLM writes any part of this.
"""
from __future__ import annotations

from datetime import datetime


def _pct(x, digits=0):
    return "n/a" if x is None else f"{float(x) * 100:.{digits}f}%"


def _num(x, digits=2):
    return "n/a" if x is None else f"{float(x):.{digits}f}"


def render(analysis: dict) -> str:
    if "error" in analysis:
        return f"# V5 analysis failed\n\n{analysis['error']}\n"

    t = analysis["ticker"]
    rec = analysis["recommendation"]
    ens = analysis["ensemble"]
    risk = analysis["risk"]
    meta = analysis["meta"]
    sa = analysis["self_audit"]
    L: list[str] = []

    # ── header ──────────────────────────────────────────────────────────────
    L += [f"# {t} — ARIA V5 Investment Committee Memo",
          "",
          f"**{rec['action']}** · confidence {rec['confidence_band']} · "
          f"risk verdict {risk['verdict']}",
          "",
          f"> {rec['headline']}",
          "",
          f"*Generated {analysis['as_of']} · {analysis['module_count']['reporting']} of "
          f"{analysis['module_count']['total']} modules reporting · weights v{analysis['weights_version']} "
          f"· {analysis['elapsed_ms']}ms*",
          ""]

    # ── the position ────────────────────────────────────────────────────────
    L += ["## 1. The position", "",
          "| | |", "|---|---|",
          f"| Direction | {ens['direction']} (net {ens['net_score']:+.1f} on -100..+100) |",
          f"| Size | {risk['position_size_pct']:.2f}% of equity "
          f"(${risk['notional']:,.0f} on ${risk['equity']:,.0f}) |",
          f"| Entry | {_num(risk['entry_price'])} |",
          f"| Stop | {_num(risk['stop_price'])} |",
          f"| Target | {_num(risk['target_price'])} |",
          f"| Capital at risk | {risk['max_loss_pct']:.2f}% of equity if the stop is hit |",
          f"| Reward:risk | {_num(risk['reward_risk'], 1)}:1 |",
          "",
          f"**Invalidation.** {risk['invalidation']}",
          "",
          f"*{rec['execution_note']}*",
          ""]

    # ── the ensemble ────────────────────────────────────────────────────────
    L += ["## 2. What the engines say", "",
          f"Weighted net score **{ens['net_score']:+.1f}**, P(bull) **{_pct(ens['p_bull'])}**, "
          f"composite confidence **{_pct(ens['confidence'])}** before meta-review.",
          "",
          f"{ens['penalties'].get('explanation', '')}",
          ""]

    L += ["### Majority", ""]
    for m in ens["majority"][:8]:
        ci = m["ci"]
        ci_s = f"[{_pct(ci[0])}, {_pct(ci[1])}]" if ci[0] is not None else "no interval"
        L.append(f"- **{m['module']}** ({m['family']}, weight {_pct(m['weight'], 1)}, "
                 f"net {m['net']:+.0f}, CI {ci_s}) — {m['strongest_evidence']}")
    L.append("")

    if ens["dissent"]:
        label = ens["agreement"].get("dissent_label", "dissent").capitalize()
        L += [f"### {label} — preserved, not deleted", ""]
        for m in ens["dissent"][:8]:
            ci = m["ci"]
            ci_s = f"[{_pct(ci[0])}, {_pct(ci[1])}]" if ci[0] is not None else "no interval"
            L.append(f"- **{m['module']}** ({m['family']}, weight {_pct(m['weight'], 1)}, "
                     f"net {m['net']:+.0f}, CI {ci_s}) — {m['strongest_evidence']}")
        L.append("")
    else:
        L += ["### Dissent", "",
              "No module took the opposite side. Unanimity across correlated engines is weaker "
              "evidence than it looks — see the counterargument below.", ""]

    if ens["agreement"].get("conflicts"):
        L += ["### Open conflicts", ""]
        L += [f"- {c}" for c in ens["agreement"]["conflicts"]]
        L.append("")

    abst = ens["agreement"].get("abstained") or []
    if abst:
        L += [f"**Abstained ({len(abst)}):** " + ", ".join(abst), "",
              "An abstention is a module reporting that it lacks the data for a calibrated view. "
              "It carries no weight in either direction.", ""]

    # ── risk ────────────────────────────────────────────────────────────────
    L += ["## 3. Risk gate", "",
          f"Verdict **{risk['verdict']}**. This layer has veto power over every engine above it.",
          ""]
    for c in risk["checks"]:
        mark = "PASS" if c["passed"] else ("VETO" if c["severity"] == "veto" else "FAIL")
        L.append(f"- **{mark}** · {c['name']} — {c['detail']}")
    L.append("")

    st = risk.get("stress") or {}
    if st and "note" not in st:
        L += ["### Stress tests", "",
              f"- Worst historical day {st.get('worst_day_pct', 0):+.1f}% → "
              f"{st.get('position_loss_worst_day_pct', 0):.2f}% of equity at this size",
              f"- Worst historical month {st.get('worst_month_pct', 0):+.1f}% → "
              f"{st.get('position_loss_worst_month_pct', 0):.2f}% of equity",
              f"- Maximum drawdown on record {st.get('max_drawdown_pct', 0):+.1f}%; currently "
              f"{st.get('current_drawdown_pct', 0):+.1f}% from the high"]
        for k in ("market_5pct_drop", "market_10pct_drop", "market_20pct_drop"):
            s = st.get(k)
            if isinstance(s, dict):
                L.append(f"- Market {k.split('_')[1]} drop (beta {s['beta']}) → instrument "
                         f"{s['instrument_move_pct']:+.1f}%, position "
                         f"{s['position_pnl_pct_of_equity']:+.2f}% of equity")
        if isinstance(st.get("vol_doubling"), dict):
            v = st["vol_doubling"]
            L.append(f"- If volatility doubles from {v['current_vol_pct']}%, size falls to "
                     f"{v['size_at_double_vol_pct']:.2f}% of equity")
        L.append("")
    for n in risk.get("notes") or []:
        L.append(f"*{n}*")
    L.append("")

    # ── meta-reasoning ──────────────────────────────────────────────────────
    L += ["## 4. Meta-reasoning — the case against", "",
          f"### Strongest counterargument", "", meta["counterargument"], ""]

    if meta.get("contradictions"):
        L += ["### Contradictions found", ""]
        L += [f"- {c}" for c in meta["contradictions"]]
        L.append("")

    L += ["### Independent reasoning paths", "",
          "These do not use the primary weighting scheme at all.", ""]
    for p in meta["alternative_paths"]:
        agree = "agrees" if p["agrees"] else "**disagrees**"
        L.append(f"- **{p['name']}** ({p['method']}) → {p['direction']}, "
                 f"P(bull) {_pct(p['p_bull'])} — {agree} with the primary. {p['detail']}")
    L += ["",
          f"**Confidence adjustment.** {meta['adjustment_reason']}",
          f" Confidence moved from {_pct(meta['confidence_before'])} to "
          f"{_pct(meta['confidence_after'])}.",
          ""]

    L += ["### Falsification tests", "",
          "Run these to break the thesis rather than to confirm it.", ""]
    L += [f"{i + 1}. {t_}" for i, t_ in enumerate(meta["falsification_tests"])]
    L.append("")

    # ── self-audit ──────────────────────────────────────────────────────────
    L += ["## 5. Self-audit", "", "### What I know", ""]
    L += [f"- {k}" for k in sa["what_i_know"]]
    L += ["", "### What I do not know", ""]
    L += [f"- {k}" for k in sa["what_i_do_not_know"]] or ["- (nothing material was unavailable)"]
    L += ["", "### Assumptions, each with its own confidence", "",
          "| Assumption | Confidence | Basis |", "|---|---|---|"]
    for a in sa["assumptions"]:
        L.append(f"| {a['assumption']} | {a['confidence']} | {a['basis']} |")
    L += ["", "### What would change this conclusion", ""]
    L += [f"- {c}" for c in sa["what_would_change_this"]]

    cr = sa["confidence_range"]
    L += ["", "### Confidence", "",
          f"- Direction: **{cr['direction']}**",
          f"- P(bull) range across reporting modules: **{_pct(cr['p_bull_range'][0])} – "
          f"{_pct(cr['p_bull_range'][1])}**",
          f"- Confidence in the recommendation: **{_pct(cr['confidence_range'][0])} – "
          f"{_pct(cr['confidence_range'][1])}**",
          f"- {cr['derivation']}",
          "", "### Blind spots and model weaknesses", ""]
    L += [f"- {b}" for b in sa["blind_spots"]]

    tr = sa.get("track_record") or {}
    if tr.get("n_predictions"):
        line = (f"- ARIA's last {tr['n_predictions']} logged calls were {_pct(tr.get('bull_share'))} "
                f"bullish")
        if tr.get("n_resolved"):
            line += f"; {tr['n_resolved']} have resolved at a {_pct(tr.get('hit_rate'))} hit rate"
            if tr.get("mean_confidence") is not None and tr.get("hit_rate") is not None:
                gap = tr["mean_confidence"] - tr["hit_rate"]
                line += (f" against {_pct(tr['mean_confidence'])} stated confidence "
                         f"({gap * 100:+.0f}pp calibration gap)")
        if tr.get("recent_streak"):
            line += f". Recent run: {tr['recent_streak']}"
        L += ["", "### Track record, stated before the next call", "", line]

    L += ["", "---", "",
          f"*ARIA V5 · deterministic render · prediction id "
          f"{analysis.get('prediction_id') or 'not logged'} · "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M')}*", ""]

    return "\n".join(L)
