"""
label_v1.py — Task 2: label headlines with Claude into the v1 schema.

    export ANTHROPIC_API_KEY=...        # in the shell only; never in a file
    /scratch/$USER/venv/bin/python label_v1.py              # label + split
    /scratch/$USER/venv/bin/python label_v1.py --split-only # re-split from raw

Resumable: every finished headline is appended to ~/data/v1/labelled_raw.jsonl
as soon as it comes back, and a rerun skips headlines already in that file.
A dropped SSH session therefore costs nothing but the in-flight requests.

Cost cap: at most 12,000 headlines are ever sent (--limit, default 12000).
Going beyond that needs --allow-over-cap, which the handover says to ask
about first.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from headline_schema import (EVENTS, JSON_SCHEMA, SENTIMENTS, TWITTER_LABELS,  # noqa: E402
                             append_results, norm_text, validate)

MODEL = "claude-haiku-5-5"
CAP = 12_000
SEED = 1
N_TEST = 300

LABELLER_PROMPT = (
    "You label financial news headlines for a trading system. For the headline "
    "given, return:\n"
    "- sentiment: the likely effect of the news on the entities it names, or on "
    "the broad market if it names none (positive / negative / neutral). Use "
    "neutral for factual or mixed news with no clear direction.\n"
    "- entities: every company, stock index, commodity or currency explicitly "
    "named in the headline, each with its own sentiment. Write the name as it "
    "appears in the headline (e.g. 'Rolls-Royce', 'FTSE 100', 'gold', 'sterling'). "
    "Never write a ticker symbol, never add an entity that is not named, and "
    "use [] when nothing is named. People, countries, government agencies "
    "and central banks are not entities.\n"
    "- event: the single best category: " + ", ".join(EVENTS) + ". "
    "mna = mergers and acquisitions; macro_data = economic releases such as "
    "inflation, jobs or GDP; commodity_move = a price move in a commodity; "
    "rating_change = analyst or credit rating changes and price targets. "
    "Use other only when none fits."
)


def load_headlines(path: Path) -> list[str]:
    seen, out = set(), []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        t = line.strip()
        k = norm_text(t)
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def load_gold() -> dict[str, str]:
    """normalised twitter text -> human sentiment, from both splits."""
    from datasets import load_dataset
    gold = {}
    ds = load_dataset("zeroshot/twitter-financial-news-sentiment")
    for split in ds.values():
        for row in split:
            gold[norm_text(row["text"])] = TWITTER_LABELS[int(row["label"])]
    return gold


def label_one(client, text: str, retries: int = 3) -> tuple[dict | None, str]:
    """Up to `retries` attempts. Returns (label, "") or (None, last_error)."""
    import anthropic
    err = ""
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=LABELLER_PROMPT,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": JSON_SCHEMA}},
                messages=[{"role": "user", "content": text}],
            )
            if resp.stop_reason != "end_turn":
                err = f"stop_reason={resp.stop_reason}"
                continue
            raw = next((b.text for b in resp.content if b.type == "text"), "")
            obj = json.loads(raw)
            ok, why = validate(obj)
            if ok:
                return obj, ""
            err = f"invalid: {why}"
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                anthropic.NotFoundError) as e:
            raise SystemExit(f"API refused the request ({type(e).__name__}); "
                             "check the key and model name. Stopping.") from e
        except anthropic.RateLimitError:
            err = "rate limited"
            time.sleep(10 * (attempt + 1))
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            err = f"{type(e).__name__}"
            time.sleep(2 ** attempt)
        except json.JSONDecodeError:
            err = "unparseable JSON"
    return None, err


def run_labelling(headlines: list[str], gold: dict, raw_path: Path, threads: int) -> None:
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set. Ask the user to "
                         "`export ANTHROPIC_API_KEY=...` in this shell.")
    # 60 s, not the SDK default of 10 min: a request that hangs on the
    # network would otherwise hold one of the 8 threads for up to half an hour.
    client = anthropic.Anthropic(max_retries=2, timeout=60.0)

    done = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("label"):          # failed rows are retried on a rerun
                done.add(norm_text(r["text"]))
    todo = [t for t in headlines if norm_text(t) not in done]
    print(f"{len(done)} already labelled, {len(todo)} to go, {threads} threads")

    lock = threading.Lock()
    n = 0
    with raw_path.open("a", encoding="utf-8") as out, \
            ThreadPoolExecutor(max_workers=threads) as pool:
        futs = {pool.submit(label_one, client, t): t for t in todo}
        for fut in as_completed(futs):
            text = futs[fut]
            label, err = fut.result()
            row = {"text": text, "label": label}
            if label is None:
                row["error"] = err
            g = gold.get(norm_text(text))
            if g:
                row["gold_sentiment"] = g
            with lock:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                n += 1
                if n % 250 == 0:
                    print(f"  {n}/{len(todo)}", flush=True)


def pct(c: Counter, total: int) -> str:
    return ", ".join(f"{k} {v} ({100 * v / total:.1f}%)" for k, v in c.most_common())


def summarise_and_split(raw_path: Path, out_dir: Path) -> str:
    # A rerun retries earlier failures, so a headline can appear twice;
    # keep one row per headline, preferring a successful label.
    by_text: dict[str, dict] = {}
    for l in raw_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(l)
        k = norm_text(r["text"])
        if k not in by_text or r.get("label"):
            by_text[k] = r
    rows = list(by_text.values())
    good = [r for r in rows if r.get("label")]
    failed = [r for r in rows if not r.get("label")]
    n = len(good)
    if not n:
        raise SystemExit("no valid labels to split")

    sent = Counter(r["label"]["sentiment"] for r in good)
    event = Counter(r["label"]["event"] for r in good)
    n_ent = Counter(min(len(r["label"]["entities"]), 4) for r in good)
    gold_rows = [r for r in good if r.get("gold_sentiment")]
    agree = sum(r["label"]["sentiment"] == r["gold_sentiment"] for r in gold_rows)
    fail_why = Counter(r.get("error", "?").split(":")[0] for r in failed)

    lines = [
        "## Task 2 — v1 labels (claude-haiku-5-5)",
        f"- labelled OK: **{n}**; dropped after 3 attempts: **{len(failed)}**"
        + (f" ({pct(fail_why, len(failed))})" if failed else ""),
        f"- top-level sentiment: {pct(sent, n)}",
        f"- event: {pct(event, n)}",
        "- entities per headline: " + ", ".join(
            f"{'4+' if k == 4 else k}: {n_ent[k]}" for k in sorted(n_ent)),
    ]
    if gold_rows:
        lines.append(f"- agreement with twitter human label (`gold_sentiment`): "
                     f"**{agree}/{len(gold_rows)} = {100 * agree / len(gold_rows):.1f}%**")
        conf = Counter((r["gold_sentiment"], r["label"]["sentiment"]) for r in gold_rows)
        lines.append("  - gold→claude: " + ", ".join(
            f"{g}→{c} {conf[(g, c)]}" for g in SENTIMENTS for c in SENTIMENTS
            if g != c and conf[(g, c)]))
    else:
        lines.append("- no headline matched the twitter dataset, so labeller "
                     "agreement could not be measured (check collect_all.py output)")
    top, top_n = sent.most_common(1)[0]
    if top_n / n > 0.60:
        lines.append(f"- ⚠ **{top} is {100 * top_n / n:.1f}% of labels (>60%).** "
                     "Proposed before training: downsample it in train.jsonl only "
                     "(test keeps the natural distribution) to at most 50%. "
                     "Waiting for the user's decision.")

    # Rows land in thread-completion order; sort first so the seeded shuffle
    # gives the same split on every machine and every rerun.
    good.sort(key=lambda r: norm_text(r["text"]))
    random.Random(SEED).shuffle(good)
    test, train = good[:N_TEST], good[N_TEST:]
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, part in (("test.jsonl", test), ("train.jsonl", train)):
        with (out_dir / name).open("w", encoding="utf-8") as f:
            for r in part:
                row = {"text": r["text"], "label": r["label"]}
                if r.get("gold_sentiment"):
                    row["gold_sentiment"] = r["gold_sentiment"]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines.append(f"- split (seed {SEED}): {len(train)} → `{out_dir}/train.jsonl`, "
                 f"{len(test)} → `{out_dir}/test.jsonl`")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headlines", type=Path, default=Path.home() / "headlines.txt")
    ap.add_argument("--out", type=Path, default=Path.home() / "data" / "v1")
    ap.add_argument("--limit", type=int, default=CAP)
    ap.add_argument("--allow-over-cap", action="store_true")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--split-only", action="store_true")
    args = ap.parse_args()

    if args.limit > CAP and not args.allow_over_cap:
        raise SystemExit(f"--limit {args.limit} exceeds the {CAP} cost cap; "
                         "ask the user, then pass --allow-over-cap")
    args.out.mkdir(parents=True, exist_ok=True)
    raw_path = args.out / "labelled_raw.jsonl"

    if not args.split_only:
        headlines = load_headlines(args.headlines)
        print(f"{len(headlines)} unique headlines in {args.headlines}")
        if len(headlines) > args.limit:
            # A seeded sample, not the first N lines: the file is in source
            # order, so its head would be all-twitter or all-RSS.
            headlines = random.Random(SEED).sample(headlines, args.limit)
            print(f"sampled {args.limit} (seed {SEED})")
        gold = load_gold()
        run_labelling(headlines, gold, raw_path, args.threads)

    md = summarise_and_split(raw_path, args.out)
    print(md)
    print(f"\nappended to {append_results(md)}")


if __name__ == "__main__":
    main()
