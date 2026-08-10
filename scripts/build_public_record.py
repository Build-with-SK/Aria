"""
scripts/build_public_record.py
==============================
Generate a single static HTML page showing this system's track record, and
nothing else about this system.

    venv\\Scripts\\python.exe scripts\\build_public_record.py
    venv\\Scripts\\python.exe scripts\\build_public_record.py --out docs/record.html

WHY STATIC, AND WHY SO LITTLE ON IT
-----------------------------------
The point of publishing a track record is that a stranger can check the claims.
That does not require giving them the API — it requires giving them the
numbers. So this writes one self-contained file with no scripts, no fetches and
no links back into the app: it can be served from anywhere, or emailed, and it
exposes no route, no ticker the owner holds, and no position.

What it deliberately DOES show is the unflattering half — the resolved count
when it is too small to mean anything, the modules that have never been
validated, and the ones that have been measured and are worse than chance. A
track record page that only appears once the numbers are good is an
advertisement.

Run it after the loop has resolved something new; it is a snapshot, and it says
so with a timestamp.
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "docs" / "public" / "track-record.html"

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; font:15px/1.6 ui-monospace,
  SFMono-Regular, Menlo, Consolas, monospace; background:#0b0d10; color:#d6d9de; }
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size:1.5rem; letter-spacing:.02em; margin:0 0 .25rem; color:#f0f2f5; }
h2 { font-size:.95rem; text-transform:uppercase; letter-spacing:.14em;
  color:#ff8c42; margin:2.5rem 0 .75rem; border-bottom:1px solid #23272e;
  padding-bottom:.4rem; }
.sub { color:#7d858f; margin:0 0 2rem; font-size:.85rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(11rem,1fr)); gap:.75rem; }
.card { background:#12151a; border:1px solid #23272e; border-radius:6px; padding:.9rem 1rem; }
.card .k { color:#7d858f; font-size:.72rem; text-transform:uppercase; letter-spacing:.1em; }
.card .v { font-size:1.5rem; color:#f0f2f5; margin-top:.25rem; }
.card .v.muted { color:#7d858f; font-size:1rem; }
.note { background:#12151a; border-left:3px solid #ff8c42; padding:.9rem 1rem;
  border-radius:0 6px 6px 0; color:#c3c8cf; }
table { width:100%; border-collapse:collapse; font-size:.82rem; }
th, td { text-align:right; padding:.4rem .5rem; border-bottom:1px solid #1c2026; }
th:first-child, td:first-child { text-align:left; }
th { color:#7d858f; font-weight:400; text-transform:uppercase; font-size:.68rem;
  letter-spacing:.08em; }
.pos { color:#4ec9a5; } .neg { color:#ff6b6b; } .dim { color:#5f6771; }
.scroll { overflow-x:auto; }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid #23272e;
  color:#5f6771; font-size:.75rem; }
@media (prefers-color-scheme: light) {
  body { background:#fbfbfc; color:#24282e; }
  h1,.card .v { color:#0d1014; } .card,.note { background:#fff; border-color:#e3e6ea; }
  th,td { border-color:#eceef1; } .sub,.card .k,th,footer { color:#6a727c; }
}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _pct(v, digits=1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _signed(v) -> str:
    if v is None:
        return '<span class="dim">—</span>'
    cls = "pos" if v > 0 else "neg" if v < 0 else "dim"
    return f'<span class="{cls}">{v * 100:+.1f}%</span>'


def build_html() -> str:
    from src.v5 import track_record, walkforward

    rec = track_record.build()
    cal = rec.get("calibration") or {}
    perf = rec.get("performance") or {}
    wf = walkforward.summary()

    measurable = bool(cal.get("measurable"))
    resolved = perf.get("resolved", 0) or 0

    cards = [
        ("Resolved calls", str(resolved), False),
        ("Hit rate", _pct(perf.get("hit_rate")) if measurable else "not yet", not measurable),
        ("Stated confidence", _pct(perf.get("mean_confidence")) if measurable else "not yet",
         not measurable),
        ("Brier skill", _pct(cal.get("skill")) if measurable else "not yet", not measurable),
        ("Modules walk-forward validated",
         f"{wf['modules_validated']}/{wf['modules_total']}", False),
    ]
    cards_html = "".join(
        f'<div class="card"><div class="k">{_e(k)}</div>'
        f'<div class="v{" muted" if muted else ""}">{_e(v)}</div></div>'
        for k, v, muted in cards)

    # Calibration buckets — only the ones with enough sample to report.
    if measurable and cal.get("buckets"):
        rows = "".join(
            f"<tr><td>{_e(b['range'])}</td><td>{b['n']}</td>"
            f"<td>{_pct(b['stated_confidence'])}</td>"
            f"<td>{_pct(b['realised_frequency']) if b['reportable'] else '<span class=dim>too few</span>'}</td>"
            f"<td>{_signed(b['gap']) if b['reportable'] else '<span class=dim>—</span>'}</td></tr>"
            for b in cal["buckets"])
        calibration_html = (
            '<div class="scroll"><table><thead><tr><th>Stated</th><th>n</th>'
            '<th>Claimed</th><th>Realised</th><th>Gap</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
            f'<p class="sub">{_e(cal.get("verdict", ""))}</p>')
    else:
        calibration_html = (
            f'<div class="note">{_e(cal.get("note") or rec["headline"]["text"])}</div>')

    # Walk-forward: every module, validated or not, worst edge first so the bad
    # news is not below the fold.
    mods = sorted(wf["modules"],
                  key=lambda m: (m["walk_forward_validated"],
                                 -(m["edge_over_base"] if m["edge_over_base"] is not None else 9)))
    def _edge_cell(m: dict) -> str:
        """An edge with no significance behind it is dimmed, not bolded.

        Without this the page led with "pca +27.6%" from eleven calls — which,
        after discounting for names that move together, is fourteen effective
        observations at most and means nothing. A reader scanning a column of
        percentages has no way to know which ones are real, so the page has to
        tell them in the cell itself.
        """
        cell = _signed(m["edge_over_base"])
        if m.get("edge_over_base") is None:
            return cell
        if m.get("walk_forward_skill_demonstrated"):
            return cell
        return f'<span class=dim title="not statistically significant">{cell}*</span>'

    def _status_cell(m: dict) -> str:
        if m.get("walk_forward_skill_demonstrated"):
            return "skill shown"
        if m.get("walk_forward_validated"):
            return '<span class=dim>measured, no skill shown</span>'
        return '<span class=dim>never measured</span>'

    mod_rows = "".join(
        f"<tr><td>{_e(m['module'])}</td><td>{_e(m['family'])}</td>"
        f"<td>{m['n_calls'] or 0}</td>"
        f"<td>{m.get('n_effective') if m.get('n_effective') is not None else '<span class=dim>—</span>'}</td>"
        f"<td>{_pct(m['hit_rate']) if m['hit_rate'] is not None else '<span class=dim>—</span>'}</td>"
        f"<td>{_edge_cell(m)}</td>"
        f"<td>{_status_cell(m)}</td>"
        "</tr>" for m in mods)

    caveats = "".join(f"<li>{_e(c)}</li>"
                      for c in (walkforward.load().get("caveats") or []))

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>ARIA — track record</title>
<style>{CSS}</style></head>
<body><main>

<h1>ARIA — track record</h1>
<p class="sub">Snapshot generated {_e(generated)}. Read-only, static, and
generated from the same API the system answers with.</p>

<div class="note">{_e(rec["headline"]["text"])}</div>

<h2>Where it stands</h2>
<div class="grid">{cards_html}</div>

<h2>Calibration — does {_e("62%")} mean {_e("62%")}?</h2>
{calibration_html}

<h2>Module validation</h2>
<p class="sub">Every research module, whether or not it has ever been tested.
Sorted worst-first among the validated ones — an unvalidated module is not a
passing module, and a module that has been measured and lost says so here.</p>
<div class="scroll"><table>
<thead><tr><th>Module</th><th>Family</th><th>Calls</th><th>Independent</th><th>Hit</th>
<th>vs base</th><th>Status</th></tr></thead>
<tbody>{mod_rows}</tbody></table></div>

<h2>How to read this</h2>
<ul class="sub">{caveats}
<li>Hit rate above 50% is not skill on its own. The market rose in most of the
measured windows, so the honest comparison is the "vs base" column.</li>
<li><strong>An edge marked * is not statistically significant</strong> — it is
inside the noise for the sample behind it, and should be read as "no
measurable effect", not as a small one.</li>
<li>The "Independent" column is the call count after discounting for names that
move together. Forty tickers on the same date are forty observations of one
market day, not forty facts. It is usually a lot smaller than "Calls", and it
is the number the statistics are actually built on.</li>
<li>Everything here is paper. No number on this page was produced by risking
money.</li>
</ul>

<footer>
Generated by scripts/build_public_record.py from /api/v5/track-record and
/api/v5/walk-forward. No live data, no positions, no holdings, and no link back
into the system. Nothing here is investment advice.
</footer>
</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help=f"output path (default {DEFAULT_OUT})")
    args = ap.parse_args()

    out = Path(args.out) if args.out else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
