"""Offline causal futures replay for research, not an execution strategy.

Always flat at each candle's close. Contract switches therefore cannot carry
positions or earn synthetic rollover jumps. Each new contract warms up afresh.
Dollar P&L includes round-trip fees and slippage on both fills.
"""
from __future__ import annotations


def replay(df, *, fee_per_side, slippage_ticks, tick_size, multiplier, scorer=None):
    import math
    import pandas as pd
    from src.data.technical_engine import closed_candles, score_candles
    if any(not math.isfinite(v) or v < 0 for v in (fee_per_side, slippage_ticks)):
        raise ValueError("nonnegative finite fees and slippage required")
    if any(not math.isfinite(v) or v <= 0 for v in (tick_size, multiplier)):
        raise ValueError("positive finite tick size and contract multiplier required")
    required = {"Open", "High", "Low", "Close", "Contract", "Expiration"}
    if not required <= set(df.columns) or df.empty:
        raise ValueError("OHLC, exact Contract and Expiration required")
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None or not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError("ordered unique timezone-aware execution-bar opens required")
    if df.Contract.isna().any() or df.Expiration.isna().any() or not df.Contract.str.match(r'^[A-Z]{1,4}[FGHJKMNQUVXZ]\d{2}\.[A-Z]+$').all():
        raise ValueError("dated provider contract identities required; no continuous futures")
    if df.groupby("Contract").Expiration.nunique().max() != 1:
        raise ValueError("conflicting contract expirations")
    if not df.index.equals(df.index.normalize()) or (df.index.to_series().diff().dropna() < pd.Timedelta(days=1)).any():
        raise ValueError("daily candle opens required for this replay")
    score = scorer or score_candles
    trades, histories, last_contract, rolls = [], {}, None, 0
    for i, (at, row) in enumerate(df.iterrows()):
        contract = row.Contract
        if last_contract is not None and contract != last_contract:
            rolls += 1
        last_contract = contract
        expiry = pd.Timestamp(row.Expiration)
        if expiry.tzinfo is None or at + pd.DateOffset(days=1) > expiry:
            raise ValueError("unverified timezone or expired contract")
        import re
        match = re.fullmatch(r"[A-Z]{1,4}([FGHJKMNQUVXZ])(\d{2})\.[A-Z]+", contract)
        if not match or match[1] != "FGHJKMNQUVXZ"[expiry.month-1] or int(match[2]) != expiry.year % 100:
            raise ValueError("contract month/year differs from expiration")
        # Validate the current bar independently: it is used only for fills.
        vals = [float(row[k]) for k in ("Open", "High", "Low", "Close")]
        if not all(math.isfinite(v) for v in vals) or vals[1] < max(vals) or vals[2] > min(vals):
            raise ValueError("malformed execution candle")
        history = histories.get(contract, [])
        if len(history) >= 200:
            past = df.iloc[history]
            # Decision is fixed before the execution candle opens.
            closed, freshness = closed_candles(past, "1d", at)
            if len(closed) < 200 or freshness["data_stale"]:
                histories.setdefault(contract, []).append(i)
                continue
            result = score(closed)
            label = result["summary"]
            direction = 1 if label in ("Buy", "Strong Buy") else -1 if label in ("Sell", "Strong Sell") else 0
            if direction:
                cost = 2 * (fee_per_side + slippage_ticks * tick_size * multiplier)
                gross = direction * (row.Close - row.Open) * multiplier
                trades.append({"at": at.isoformat(), "contract": contract,
                               "decision_bar": closed.index[-1].isoformat(), "direction": direction,
                               "gross_pnl": float(gross), "cost": cost, "net_pnl": float(gross - cost)})
        histories.setdefault(contract, []).append(i)
    return {"trades": trades, "net_pnl": sum(t["net_pnl"] for t in trades),
            "gross_pnl": sum(t["gross_pnl"] for t in trades), "fees_and_slippage": sum(t["cost"] for t in trades),
            "contract_switches": rolls, "rollover_policy": "flat each close; no cross-contract P&L; warm-up per contract",
            "limitation": "Illustrative open-to-close rule; no out-of-sample edge established. No positions or orders created."}
