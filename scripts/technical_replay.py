"""Run a cost-inclusive offline experiment on owner-supplied verified daily CSV.

Example: python scripts/technical_replay.py history.csv --fee 2.5
         --slippage-ticks 1 --tick-size .1 --multiplier 100
Required columns: Timestamp, Open, High, Low, Close, Contract, Expiration.
Timestamps and expirations must include explicit UTC offsets. No data download.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    import pandas as pd
    from src.backtesting.technical_replay import replay
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--fee", type=float, required=True, help="Fee per contract per side")
    parser.add_argument("--slippage-ticks", type=float, required=True)
    parser.add_argument("--tick-size", type=float, required=True)
    parser.add_argument("--multiplier", type=float, required=True)
    args = parser.parse_args()
    frame = pd.read_csv(args.csv)
    for column in ("Timestamp", "Expiration"):
        if column not in frame or not frame[column].map(lambda v: bool(re.search(r"(?:Z|[+-]\d{2}:\d{2})$", str(v)))).all():
            parser.error(f"{column} requires explicit timezone offsets")
    frame.index = pd.to_datetime(frame.pop("Timestamp"), utc=True)
    report = replay(frame, fee_per_side=args.fee, slippage_ticks=args.slippage_ticks,
                    tick_size=args.tick_size, multiplier=args.multiplier)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
