import sys
sys.path.insert(0, sys.argv[1])
import pandas as pd, numpy as np
from src.backtesting.backtester import run_backtest
n = 150
idx = pd.date_range("2025-01-01", periods=n, freq="D")
df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
                   "dist_sma_20": 0.1, "dist_sma_50": 0.1, "dist_sma_200": 0.1, "rsi": 100.0,
                   "macd_histogram": 1.0}, index=idx)
# Bar 120 opens at 100 and closes at 70: the -30% close trips the 20% drawdown breaker.
df.iloc[120, df.columns.get_loc("Close")] = 70.0
df.iloc[120, df.columns.get_loc("Low")] = 69.0
df.iloc[121:, df.columns.get_loc("Open")] = 70.0
df.iloc[121:, df.columns.get_loc("Close")] = 70.0
r = run_backtest("PROBE", df, commission_pct=0, slippage_pct=0, risk_per_trade=1.0,
                 stop_loss_pct=0.5, max_drawdown_limit=0.20)
t = [x for x in r.trades if x.exit_reason == "max_drawdown"][0]
print("breaker exit date", t.exit_date, "exit price", t.exit_price, "pnl_pct", t.pnl_pct,
      "| bar close that triggered it =", df.Close.iloc[120])
print("final equity", round(r.equity_curve.iloc[-1], 2), "(an honest exit at the close would leave ~70000)")
# Stop at close vs intrabar: low pierces -5% stop but close recovers -> no stop recorded
df2 = df.copy(); df2["Close"] = 100.0; df2["Open"] = 100.0
df2.iloc[120, df2.columns.get_loc("Low")] = 90.0   # -10% intrabar, closes flat
r2 = run_backtest("PROBE", df2, commission_pct=0, slippage_pct=0, stop_loss_pct=0.05)
print("stop exits when low hit -10% but close flat:", sum(x.exit_reason == "stop_loss" for x in r2.trades))
