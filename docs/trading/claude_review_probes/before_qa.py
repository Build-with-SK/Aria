"""Run inside the 7385130 extraction: reproduce QA-02 and QA-04 as reviewed.

QA-01 and QA-03 are reproduced by probe_bt.py (same folder).
"""
import importlib.util
import inspect
import json
import sys
from decimal import Decimal as D

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from src.backtesting import experiments, metrics  # noqa: E402

idx = pd.date_range("2025-01-01", periods=366, freq="D")          # 365 daily crypto returns
curve = pd.Series(100 * 2 ** (np.arange(366) / 365), index=idx)    # doubles in exactly one year
out = {
    "QA02_cagr_of_a_one_year_doubling": round(metrics.cagr(curve), 4),
    "QA02_years_assumed": round(len(curve) / 252, 4),
    "QA02_sharpe_accepts_a_frequency": "periods_per_year" in inspect.signature(metrics.sharpe_ratio).parameters,
    "QA04_path_replay_module": importlib.util.find_spec("src.backtesting.challenge_replay") is not None,
    "QA04_backtesting_functions": sorted(n for n, f in vars(experiments).items()
                                         if inspect.isfunction(f) and f.__module__ == experiments.__name__),
    # The review's day: dips -5.1% intraday, closes -3%. The only resampler sees the close.
    "QA04_block_bootstrap_on_that_day": experiments.block_bootstrap((D("-300"),), block_size=1, samples=1),
}
print(json.dumps(out, indent=1, default=str))
