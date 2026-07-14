#!/usr/bin/env python3
# main.py
# Trading Intelligence System — Phase 5 / 6
# Full multi-strategy, multi-asset platform
# encoding: utf-8

import logging
import os
import sys
import json
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
import pandas as pd

# ── Suppress noisy library warnings (real errors still log via logger) ────────
warnings.filterwarnings("ignore", message=".*does not have valid feature names.*")
warnings.filterwarnings("ignore", message=".*Only one class is present.*")
warnings.filterwarnings("ignore", message=".*UndefinedMetricWarning.*")
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

# ── Political Intelligence Layer (optional — not yet built) ───────────────────
try:
    from src.political.political_main_integration import run_political_layer
    _POLITICAL_AVAILABLE = True
except ImportError:
    _POLITICAL_AVAILABLE = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("tis_run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("TIS")


# ── Config ────────────────────────────────────────────────────────────────────
def load_config(path: str = "configs/universe.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_tickers(config: dict) -> list:
    """Flatten all tickers from all asset classes in universe.yaml."""
    tickers = []
    universe = config.get("universe", {})
    for asset_class, assets in universe.items():
        if isinstance(assets, list):
            for asset in assets:
                if isinstance(asset, dict) and "ticker" in asset:
                    tickers.append(asset["ticker"])
                elif isinstance(asset, str):
                    tickers.append(asset)
    return list(dict.fromkeys(tickers))  # deduplicate, preserve order


def get_ticker_names(config: dict) -> Dict[str, str]:
    names = {}
    for assets in config.get("universe", {}).values():
        if isinstance(assets, list):
            for asset in assets:
                if isinstance(asset, dict):
                    names[asset.get("ticker", "")] = asset.get("name", asset.get("ticker", ""))
    return names


# ── Data download ─────────────────────────────────────────────────────────────
def _call_provider(provider, ticker: str, period: str, interval: str):
    """
    Auto-discover the real download method on YFinanceProvider.
    Tries all known method signatures from Phase 1-4 builds.
    """
    for method_name in ("download", "get_data", "fetch", "get", "get_ohlcv", "get_historical"):
        method = getattr(provider, method_name, None)
        if method is not None:
            try:
                df = method(ticker, period=period, interval=interval)
                if df is not None and not df.empty:
                    return df
            except TypeError:
                try:
                    df = method(ticker, period, interval)
                    if df is not None and not df.empty:
                        return df
                except Exception:
                    pass
            except Exception:
                pass

    # Last resort: call yfinance directly
    import yfinance as yf
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    if df is not None and not df.empty:
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        return df
    return None


def download_all_data(tickers: list, config: dict) -> Dict[str, pd.DataFrame]:
    try:
        from src.data.data_downloader import YFinanceProvider
        provider = YFinanceProvider()
    except Exception as e:
        logger.warning(f"YFinanceProvider import failed ({e}) — using yfinance directly.")
        provider = None

    period   = config.get("data", {}).get("period",   "2y")
    interval = config.get("data", {}).get("interval", "1d")
    data     = {}
    logger.info(f"Downloading data for {len(tickers)} tickers...")

    for ticker in tickers:
        try:
            if provider is not None:
                df = _call_provider(provider, ticker, period, interval)
            else:
                import yfinance as yf
                df = yf.download(ticker, period=period, interval=interval,
                                 auto_adjust=True, progress=False)
                if df is not None and hasattr(df.columns, "levels"):
                    df.columns = df.columns.get_level_values(0)

            if df is not None and not df.empty:
                data[ticker] = df
                logger.debug(f"  ✓ {ticker}: {len(df)} bars")
            else:
                logger.warning(f"  ✗ {ticker}: no data returned")
        except Exception as e:
            logger.warning(f"  ✗ {ticker}: {e}")

    logger.info(f"Downloaded {len(data)}/{len(tickers)} tickers successfully.")
    return data


# ── Feature engineering ───────────────────────────────────────────────────────
def _find_indicators_fn():
    """Auto-discover the indicators function from data_downloader."""
    try:
        import src.data.data_downloader as mod
        for name in ("add_indicators", "compute_indicators", "add_features",
                     "build_features", "engineer_features", "add_technical_indicators"):
            fn = getattr(mod, name, None)
            if fn is not None:
                return fn
    except Exception:
        pass
    return None


def _builtin_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full built-in indicator suite — runs entirely in main.py so we never
    depend on the old data_downloader signature.
    """
    df    = df.copy()
    close = df["Close"]
    high  = df.get("High",   close)
    low   = df.get("Low",    close)
    vol   = df.get("Volume", pd.Series(0, index=df.index))

    # SMAs
    df["sma_20"]  = close.rolling(20).mean()
    df["sma_50"]  = close.rolling(50).mean()
    df["sma_200"] = close.rolling(200).mean()

    # EMAs + MACD
    df["ema_12"]      = close.ewm(span=12, adjust=False).mean()
    df["ema_26"]      = close.ewm(span=26, adjust=False).mean()
    df["macd"]        = df["ema_12"] - df["ema_26"]
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, float("nan"))
    df["rsi"] = 100 - (100 / (1 + rs))

    # Bollinger Bands
    df["bb_mid"]   = close.rolling(20).mean()
    df["bb_std"]   = close.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, float("nan"))

    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # Volume trend
    vol_ma         = vol.rolling(20).mean().replace(0, float("nan"))
    df["volume_trend"] = vol / vol_ma

    # Returns
    df["ret_1d"]  = close.pct_change(1)
    df["ret_5d"]  = close.pct_change(5)
    df["ret_20d"] = close.pct_change(20)

    # Regime flags
    df["bull_market"]     = ((close > df["sma_50"]) & (df["sma_50"] > df["sma_200"])).astype(int)
    df["bear_market"]     = ((close < df["sma_50"]) & (df["sma_50"] < df["sma_200"])).astype(int)
    df["high_vol_regime"] = (df["bb_width"] > df["bb_width"].rolling(252).quantile(0.75)).astype(int)

    return df


def compute_features(raw_data: Dict[str, pd.DataFrame], config: dict) -> Dict[str, pd.DataFrame]:
    """Add technical indicators to all tickers."""
    fn       = _find_indicators_fn()
    featured = {}
    for ticker, df in raw_data.items():
        try:
            featured[ticker] = fn(df) if fn else _builtin_indicators(df)
        except Exception as e:
            logger.debug(f"Indicators failed for {ticker}: {e}")
            featured[ticker] = _builtin_indicators(df)
    return featured


# ── Signal scores ─────────────────────────────────────────────────────────────
def compute_signal_scores(featured_data: Dict[str, pd.DataFrame], config: dict) -> Dict[str, float]:
    scores = {}
    for ticker, df in featured_data.items():
        try:
            scores[ticker] = round(float(_signal_from_df(df, config)), 2)
        except Exception:
            scores[ticker] = 0.0
    return scores


def _signal_from_df(df: pd.DataFrame, config: dict) -> float:
    """Composite signal: trend + momentum + MACD + volume."""
    try:
        if df is None or df.empty or len(df) < 20:
            return 0.0
        score = 0.0

        if "sma_20" in df.columns and "sma_50" in df.columns:
            price = float(df["Close"].iloc[-1])
            sma20 = float(df["sma_20"].iloc[-1])
            sma50 = float(df["sma_50"].iloc[-1])
            if price > sma20 > sma50:
                score += 30
            elif price < sma20 < sma50:
                score -= 30
            elif price > sma20:
                score += 10

        if "rsi" in df.columns:
            rsi = float(df["rsi"].iloc[-1])
            if 40 < rsi < 60:
                score += 5
            elif rsi < 30:
                score += 15
            elif rsi > 70:
                score -= 15

        if "macd" in df.columns and "macd_signal" in df.columns:
            score += 15 if float(df["macd"].iloc[-1]) > float(df["macd_signal"].iloc[-1]) else -15

        if "volume_trend" in df.columns:
            score += 10 if float(df["volume_trend"].iloc[-1]) > 1 else -5

        return float(max(min(score, 100), -100))
    except Exception:
        return 0.0


# ── yfinance info (fundamentals) ──────────────────────────────────────────────
def fetch_yf_info(tickers: list, max_tickers: int = 30) -> Dict[str, dict]:
    info_map        = {}
    equity_tickers  = [t for t in tickers
                       if not any(t.endswith(s) for s in ["=X", "-USD", "=F"])][:max_tickers]
    logger.info(f"Fetching fundamentals for {len(equity_tickers)} equities...")
    import yfinance as yf
    for ticker in equity_tickers:
        try:
            info = yf.Ticker(ticker).info
            if info:
                info_map[ticker] = info
        except Exception:
            pass
    return info_map


# ── Macro snapshot ────────────────────────────────────────────────────────────
def run_macro(config: dict):
    try:
        from src.macro.macro_data import fetch_macro_snapshot
        snapshot = fetch_macro_snapshot()
        logger.info(f"Macro snapshot: score={getattr(snapshot, 'macro_score', 'N/A')}, "
                    f"regime={getattr(snapshot, 'regime_label', 'N/A')}")
        return snapshot
    except Exception as e:
        logger.warning(f"Macro snapshot failed: {e}")
        return None


# ── Sentiment ─────────────────────────────────────────────────────────────────
def run_sentiment(tickers: list, config: dict) -> dict:
    try:
        from src.sentiment.sentiment_analyzer import analyze_all_sentiment
        raw = analyze_all_sentiment(tickers)

        # Handle every possible return format gracefully
        if isinstance(raw, dict):
            # Already {ticker: result} — ideal
            results = raw

        elif isinstance(raw, list):
            # List of SentimentResult objects or dicts
            results = {}
            for item in raw:
                if hasattr(item, "ticker"):
                    # SentimentResult dataclass
                    results[item.ticker] = {
                        "score":         getattr(item, "score",         0.0),
                        "article_count": getattr(item, "article_count", 0),
                        "label":         getattr(item, "label",         "neutral"),
                    }
                elif isinstance(item, dict):
                    ticker = item.get("ticker") or item.get("symbol") or item.get("name")
                    if ticker:
                        results[ticker] = item
        else:
            results = {}

        logger.info(f"Sentiment analysis complete for {len(results)} tickers.")
        return results
    except Exception as e:
        logger.warning(f"Sentiment analysis failed: {e}")
        return {}


# ── ML models ─────────────────────────────────────────────────────────────────
def run_ml(featured_data: Dict[str, pd.DataFrame], config: dict) -> dict:
    try:
        from src.models.model_trainer import ModelTrainer
        trainer = ModelTrainer(config)
        results = trainer.train_and_predict_all(featured_data)
        logger.info(f"ML models trained for {len(results)} tickers.")
        return results
    except Exception as e:
        logger.warning(f"ML training failed: {e}")
        return {}


# ── Backtester ────────────────────────────────────────────────────────────────
def run_backtest(featured_data: Dict[str, pd.DataFrame],
                 signal_scores: Dict[str, float], config: dict) -> dict:
    try:
        from src.backtesting.backtester import Backtester
        from src.backtesting.metrics import compute_metrics
        bt          = Backtester(config)
        all_results = {}
        for ticker, df in featured_data.items():
            score = signal_scores.get(ticker, 0.0)
            try:
                result  = bt.run(df, score)
                metrics = compute_metrics(result)
                all_results[ticker] = {"result": result, "metrics": metrics}
            except Exception:
                pass
        logger.info(f"Backtested {len(all_results)} tickers.")
        return all_results
    except Exception as e:
        logger.warning(f"Backtester failed: {e}")
        return {}


# ── Strategy modules ──────────────────────────────────────────────────────────
def run_all_strategies(
    featured_data: Dict[str, pd.DataFrame],
    signal_scores: Dict[str, float],
    config: dict,
    macro_snapshot=None,
    sentiment_results: dict = None,
    yf_info: dict = None,
) -> Dict[str, Any]:
    results = {}

    logger.info("▶ Strategy 1/14: Equity Long/Short")
    try:
        from src.strategies.equity_long_short import run_equity_long_short
        results["equity_long_short"] = run_equity_long_short(
            featured_data, config, signal_scores, yf_info)
        r = results["equity_long_short"]
        logger.info(f"  ✓ L/S: {r.long_count}L / {r.short_count}S")
    except Exception as e:
        logger.warning(f"  ✗ equity_long_short: {e}")

    logger.info("▶ Strategy 2/14: Relative Value (Pairs)")
    try:
        from src.strategies.relative_value import run_relative_value
        results["relative_value"] = run_relative_value(featured_data, config)
        logger.info(f"  ✓ Pairs found: {len(results['relative_value'])}")
    except Exception as e:
        logger.warning(f"  ✗ relative_value: {e}")

    logger.info("▶ Strategy 3/14: Market Neutral")
    try:
        from src.strategies.market_neutral import run_market_neutral
        results["market_neutral"] = run_market_neutral(
            featured_data, config, signal_scores, yf_info)
        logger.info(f"  ✓ Net beta: {results['market_neutral'].net_beta:.3f}")
    except Exception as e:
        logger.warning(f"  ✗ market_neutral: {e}")

    logger.info("▶ Strategy 4/14: Global Macro")
    try:
        from src.strategies.global_macro import run_global_macro
        results["global_macro"] = run_global_macro(config, macro_snapshot)
        logger.info(f"  ✓ Regime: {results['global_macro'].regime}")
    except Exception as e:
        logger.warning(f"  ✗ global_macro: {e}")

    logger.info("▶ Strategy 5/14: Event Driven")
    try:
        from src.strategies.event_driven import run_event_driven
        results["event_driven"] = run_event_driven(
            featured_data, config, sentiment_results)
        logger.info(f"  ✓ Events: {results['event_driven'].upcoming_count}")
    except Exception as e:
        logger.warning(f"  ✗ event_driven: {e}")

    logger.info("▶ Strategy 6/14: Distressed/Opportunistic")
    try:
        from src.strategies.distressed import run_distressed
        results["distressed"] = run_distressed(
            featured_data, config, yf_info, sentiment_results)
        r = results["distressed"]
        logger.info(f"  ✓ Shorts: {len(r.short_candidates)} | Longs: {len(r.long_candidates)}")
    except Exception as e:
        logger.warning(f"  ✗ distressed: {e}")

    logger.info("▶ Strategy 7/14: Convertible Arb")
    try:
        from src.strategies.convertible_arb import run_convertible_arb
        results["convertible_arb"] = run_convertible_arb(featured_data, config)
        logger.info(f"  ✓ Positions: {len(results['convertible_arb'])}")
    except Exception as e:
        logger.warning(f"  ✗ convertible_arb: {e}")

    logger.info("▶ Strategy 8/14: Quant Systematic (Factors)")
    try:
        from src.strategies.quant_systematic import run_quant_systematic
        results["quant_systematic"] = run_quant_systematic(
            featured_data, config, yf_info)
        logger.info(f"  ✓ Factor longs: {len(results['quant_systematic'].long_names)}")
    except Exception as e:
        logger.warning(f"  ✗ quant_systematic: {e}")

    logger.info("▶ Strategy 9/14: Credit Long/Short")
    try:
        from src.strategies.credit_long_short import run_credit_long_short
        results["credit_long_short"] = run_credit_long_short(
            featured_data, config, macro_snapshot)
        r = results["credit_long_short"]
        logger.info(f"  ✓ HY: {r.hy_position} | IG: {r.ig_position}")
    except Exception as e:
        logger.warning(f"  ✗ credit_long_short: {e}")

    logger.info("▶ Strategy 10/14: Options Engine")
    try:
        from src.strategies.options_engine import run_options_engine
        results["options"] = run_options_engine(featured_data, config, signal_scores)
        logger.info(f"  ✓ Options signals: {len(results['options'])}")
    except Exception as e:
        logger.warning(f"  ✗ options_engine: {e}")

    logger.info("▶ Strategy 11/14: Futures Engine")
    try:
        from src.strategies.futures_engine import run_futures_engine
        results["futures"] = run_futures_engine(featured_data, config)
        logger.info(f"  ✓ Futures signals: {len(results['futures'])}")
    except Exception as e:
        logger.warning(f"  ✗ futures_engine: {e}")

    logger.info("▶ Strategy 12/14: Crypto Engine")
    try:
        from src.strategies.crypto_engine import run_crypto_engine
        results["crypto"] = run_crypto_engine(featured_data, config)
        logger.info(f"  ✓ Crypto signals: {len(results['crypto'])}")
    except Exception as e:
        logger.warning(f"  ✗ crypto_engine: {e}")

    logger.info("▶ Strategy 13/14: Forex Engine")
    try:
        from src.strategies.forex_engine import run_forex_engine
        results["forex"] = run_forex_engine(featured_data, config, macro_snapshot)
        logger.info(f"  ✓ FX signals: {len(results['forex'])}")
    except Exception as e:
        logger.warning(f"  ✗ forex_engine: {e}")

    logger.info("▶ Strategy 14/14: Derivatives Risk Engine")
    try:
        from src.strategies.derivatives_risk import run_derivatives_risk
        results["derivatives_risk"] = run_derivatives_risk(
            featured_data, config, results.get("options"))
        dr = results["derivatives_risk"]
        logger.info(f"  ✓ VaR 1d: ${dr.var_1d_99:,.0f} | CVaR: ${dr.cvar_1d_99:,.0f}")
    except Exception as e:
        logger.warning(f"  ✗ derivatives_risk: {e}")

    return results


# ── Multi-Strategy Allocator ──────────────────────────────────────────────────
def run_allocator(strategy_results: Dict[str, Any], config: dict):
    try:
        from src.strategies.multi_strategy import run_multi_strategy
        scores = {}
        for name, result in strategy_results.items():
            if hasattr(result, "strategy_score"):
                scores[name] = float(result.strategy_score)
            elif hasattr(result, "combined_score"):
                scores[name] = float(result.combined_score)
            elif isinstance(result, list) and result:
                convs = [getattr(r, "conviction", 0) for r in result]
                dirs  = [getattr(r, "direction", "neutral") for r in result]
                if convs:
                    long_bias = (sum(1 for d in dirs if d in ("long", "strong_long"))
                                 - sum(1 for d in dirs if d in ("short", "strong_short")))
                    scores[name] = float(max(min(
                        float(sum(convs) / len(convs)) * (1 if long_bias >= 0 else -1),
                        100), -100))
                else:
                    scores[name] = 0.0
            else:
                scores[name] = 0.0

        allocation = run_multi_strategy(scores, config)
        logger.info(f"Multi-strategy allocator: combined score={allocation.combined_score:.1f}, "
                    f"mode={allocation.weighting_mode}, "
                    f"est. Sharpe={allocation.estimated_portfolio_sharpe:.2f}")
        return allocation
    except Exception as e:
        logger.warning(f"Multi-strategy allocator failed: {e}")
        return None


# ── Master Portfolio ──────────────────────────────────────────────────────────
def run_master(strategy_results: Dict[str, Any], allocation, risk_report, config: dict):
    try:
        from src.strategies.master_portfolio import run_master_portfolio, format_obsidian_briefing
        master   = run_master_portfolio(config, strategy_results, allocation, risk_report)
        briefing = format_obsidian_briefing(master, config)
        logger.info(f"Master Portfolio: {len(master.top_longs)} longs, "
                    f"{len(master.top_shorts)} shorts, "
                    f"combined score={master.combined_score:.1f}")
        return master, briefing
    except Exception as e:
        logger.warning(f"Master portfolio failed: {e}")
        return None, ""


# ── Obsidian export ───────────────────────────────────────────────────────────
def write_obsidian_notes(
    config: dict,
    master_briefing: str,
    signal_scores: Dict[str, float],
    macro_snapshot=None,
    sentiment_results: dict = None,
    strategy_results: Dict[str, Any] = None,
    master_portfolio=None,
):
    try:
        obsidian_path = config.get("obsidian", {}).get("vault_path", "")
        tis_path      = config.get("obsidian", {}).get("tis_folder", "01 - Trading/TIS")

        if not obsidian_path:
            logger.warning("No obsidian.vault_path in config — skipping Obsidian write.")
            return

        base    = Path(obsidian_path) / tis_path
        base.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        _write_file(base / "latest_signals.md",
                    master_briefing or _fallback_signals(signal_scores, now_str))
        _write_file(base / "risk_summary.md",
                    _build_risk_summary(master_portfolio, strategy_results, now_str))
        _write_file(base / "model_status.md",
                    _build_model_status(signal_scores, macro_snapshot, now_str))

        log_entry = f"\n## {now_str}\n- Run complete. {len(signal_scores)} tickers processed.\n"
        if master_portfolio:
            log_entry += f"- Combined score: {master_portfolio.combined_score:.1f}\n"
            log_entry += (f"- Top long: "
                          f"{master_portfolio.top_longs[0].ticker if master_portfolio.top_longs else 'N/A'}\n")
        _append_file(base / "system_log.md", log_entry)

        logger.info(f"✅ Obsidian notes written to {base}")
    except Exception as e:
        logger.error(f"Obsidian write failed: {e}")


def _write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _append_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)


def _fallback_signals(signal_scores: Dict[str, float], now_str: str) -> str:
    sorted_scores = sorted(signal_scores.items(), key=lambda x: x[1], reverse=True)
    lines = [
        f"# Latest Signals — {now_str}", "",
        "#trading #market-intelligence #capitalwithsk #daily-scan #pulse", "",
        "| Ticker | Score | Direction |",
        "|--------|-------|-----------|",
    ]
    for ticker, score in sorted_scores[:20]:
        direction = "🟢 Long" if score > 15 else ("🔴 Short" if score < -15 else "🟡 Neutral")
        lines.append(f"| {ticker} | {score:.1f} | {direction} |")
    return "\n".join(lines)


def _build_risk_summary(master, strategy_results, now_str: str) -> str:
    lines = [f"# Risk Summary — {now_str}", "",
             "#trading #market-intelligence #capitalwithsk", ""]
    if master:
        lines += [
            "## Portfolio Risk", "",
            "| Metric | Value |", "|--------|-------|",
            f"| 1d 99% VaR | ${master.portfolio_var:,.0f} |",
            f"| CVaR | ${master.portfolio_cvar:,.0f} |",
            f"| Est. Sharpe | {master.estimated_sharpe:.2f} |",
            f"| Expected Return | {master.expected_return_pct:.2f}% |", "",
            "## Risk Budget Utilisation", "",
            "| Strategy | Allocated | Utilised |",
            "|----------|-----------|----------|",
        ]
        for b in master.risk_budgets[:10]:
            lines.append(f"| {b.strategy} | {b.allocated_pct:.0f}% | {b.utilised_pct:.0f}% |")
    else:
        lines.append("*Risk report unavailable this run.*")
    return "\n".join(lines)


def _build_model_status(signal_scores: Dict[str, float], macro_snapshot, now_str: str) -> str:
    lines = [f"# Model Status — {now_str}", "",
             "#trading #market-intelligence #capitalwithsk", "",
             f"**Tickers processed:** {len(signal_scores)}", ""]
    if macro_snapshot:
        lines += [
            f"**Macro score:** {getattr(macro_snapshot, 'macro_score', 'N/A')}",
            f"**Regime:** {getattr(macro_snapshot, 'regime_label', 'N/A')}",
            f"**VIX:** {getattr(macro_snapshot, 'vix', 'N/A')}", "",
        ]
    bullish = sum(1 for s in signal_scores.values() if s > 15)
    bearish = sum(1 for s in signal_scores.values() if s < -15)
    neutral = len(signal_scores) - bullish - bearish
    lines += [
        f"**Signal distribution:** 🟢 {bullish} bullish | 🟡 {neutral} neutral | 🔴 {bearish} bearish",
        "", f"*Generated: {now_str}*",
    ]
    return "\n".join(lines)


# ── Auto-propose trades ───────────────────────────────────────────────────────
def _auto_propose_trades(signal_scores: dict, macro_snapshot, config: dict):
    """
    After each pipeline run, push the top high-conviction signals into the
    approval queue so the user can review and approve them in the Execute page.

    Only proposes NEW tickers — skips any that already have a pending trade.
    """
    try:
        signals_path = Path("data/signals.json")
        if not signals_path.exists():
            return

        signals = json.loads(signals_path.read_text(encoding="utf-8"))

        from src.execution.approval_queue import ApprovalQueue
        from src.execution.broker_base import AssetClass, OrderRequest, OrderSide, OrderType
        queue = ApprovalQueue()

        # Tickers already pending — don't double-propose
        existing = {t.ticker for t in queue.get_all(limit=500) if t.status == "pending"}

        # Account equity for position sizing (Alpaca paper = $100k by default)
        account_equity = float(config.get("execution", {}).get("account_equity", 100_000))

        # Thresholds from config, with safe defaults
        ex_cfg    = config.get("execution", {})
        min_score = float(ex_cfg.get("min_signal_score", 25))
        max_trades = int(ex_cfg.get("max_auto_proposals", 5))

        # Eligible asset classes for Alpaca (no futures/forex — those need IBKR)
        alpaca_classes = {"equities", "etfs", "crypto"}

        # Collect candidates
        candidates = []
        for ticker, sig in signals.items():
            action = sig.get("action", "Hold")
            score  = sig.get("composite_score", 0)
            ac     = sig.get("asset_class", "equities")
            conf   = sig.get("confidence", "Low")

            if action not in ("Buy", "Sell"):
                continue
            if abs(score) < min_score:
                continue
            if ac not in alpaca_classes:
                continue
            if ticker in existing:
                continue

            candidates.append((abs(score), ticker, sig))

        # Top N by signal strength
        candidates.sort(reverse=True)
        proposed = 0
        for _, ticker, sig in candidates[:max_trades]:
            try:
                price    = float(sig.get("current_price", 0) or 0)
                if price <= 0:
                    continue

                pct      = float(sig.get("position_size_pct", 1.0)) / 100.0
                notional = account_equity * min(pct, 0.05)   # cap 5% per trade
                qty      = max(1, round(notional / price, 4))

                action   = sig.get("action", "Buy")
                side     = "buy" if action == "Buy" else "sell"
                ac_str   = sig.get("asset_class", "equities")
                ac_map   = {"equities": "equity", "etfs": "equity", "crypto": "crypto"}
                asset_cl = ac_map.get(ac_str, "equity")

                sl  = sig.get("stop_loss")
                tp  = sig.get("take_profit")
                score = sig.get("composite_score", 0)

                drivers = sig.get("drivers", [])
                bull_case = "; ".join(d for d in drivers if "above" in d.lower() or "positive" in d.lower() or "bull" in d.lower())[:200]
                bear_case = "; ".join(d for d in drivers if "below" in d.lower() or "negative" in d.lower() or "bear" in d.lower())[:200]

                req = OrderRequest(
                    ticker=ticker,
                    side=OrderSide(side),
                    qty=qty,
                    order_type=OrderType.MARKET,
                    asset_class=AssetClass(asset_cl),
                    stop_loss=sl,
                    take_profit=tp,
                    signal_score=score,
                    situation=sig.get("regime", ""),
                    thesis_summary=f"Signal score {score:+.1f} | {sig.get('confidence','?')} confidence | {sig.get('asset_class','')}",
                )

                queue.push(
                    req,
                    confidence=sig.get("confidence", ""),
                    bull_case=bull_case or f"Score {score:+.1f}, bullish_prob={sig.get('bullish_prob',0):.0%}",
                    bear_case=bear_case or f"bearish_prob={sig.get('bearish_prob',0):.0%}",
                    invalidation=f"Price below ${sig.get('invalidation', sl):.2f}" if sig.get("invalidation") else "",
                    current_price=price,
                    broker="alpaca",
                )
                proposed += 1
                logger.info(f"  ✓ Proposed {side.upper()} {qty} {ticker} @ ${price:.2f} (score {score:+.1f})")

            except Exception as e:
                logger.warning(f"  Auto-propose failed for {ticker}: {e}")

        if proposed:
            logger.info(f"\n  EXECUTION QUEUE: {proposed} new trade(s) pending your approval.")
            logger.info("  Open http://localhost:3000/execute to review.")
        else:
            logger.info("  No new high-conviction trades to propose this run.")

    except Exception as e:
        logger.warning(f"Auto-propose step failed: {e}")


# ── Main entry point ──────────────────────────────────────────────────────────
def main():
    logger.info("=" * 70)
    logger.info("  TRADING INTELLIGENCE SYSTEM — Phase 5 / 6")
    logger.info("  Multi-Strategy | Multi-Asset | Full Platform")
    logger.info("=" * 70)

    # 1. Config
    config  = load_config("configs/universe.yaml")
    tickers = get_all_tickers(config)
    logger.info(f"Universe: {len(tickers)} tickers loaded.")

    # 2. Data download
    raw_data = download_all_data(tickers, config)

    # 3. Feature engineering
    featured_data = compute_features(raw_data, config)

    # 4. Signal scores
    signal_scores = compute_signal_scores(featured_data, config)

    # 5. Macro snapshot
    macro_snapshot = run_macro(config)

    # 6. Sentiment
    sentiment_results = run_sentiment(tickers, config)

    # 7. ML models
    ml_results = {}
    if config.get("ml", {}).get("enabled", True):
        ml_results = run_ml(featured_data, config)

    # 8. Backtester (off by default — set run_backtest: true in universe.yaml)
    bt_results = {}
    if config.get("backtest", {}).get("run_backtest", False):
        bt_results = run_backtest(featured_data, signal_scores, config)

    # 9. Fundamentals
    yf_info = {}
    if config.get("data", {}).get("fetch_fundamentals", True):
        yf_info = fetch_yf_info(tickers)

    # 10. All 14 strategy modules
    logger.info("")
    logger.info("━" * 60)
    logger.info("  RUNNING STRATEGY MODULES")
    logger.info("━" * 60)
    strategy_results = run_all_strategies(
        featured_data=featured_data,
        signal_scores=signal_scores,
        config=config,
        macro_snapshot=macro_snapshot,
        sentiment_results=sentiment_results,
        yf_info=yf_info,
    )

    # 11. Multi-strategy allocator
    logger.info("")
    logger.info("━" * 60)
    logger.info("  MULTI-STRATEGY ALLOCATOR")
    logger.info("━" * 60)
    allocation = run_allocator(strategy_results, config)

    # 12. Master portfolio
    logger.info("")
    logger.info("━" * 60)
    logger.info("  MASTER PORTFOLIO SYNTHESIS")
    logger.info("━" * 60)
    risk_report      = strategy_results.get("derivatives_risk")
    master, briefing = run_master(strategy_results, allocation, risk_report, config)

    # 13. Political Intelligence Layer (future phase — wires in when built)
    if _POLITICAL_AVAILABLE:
        logger.info("")
        logger.info("━" * 60)
        logger.info("  POLITICAL INTELLIGENCE LAYER")
        logger.info("━" * 60)
        _pol_signals = {
            t: {"technical_score": round((s + 100) / 2, 1)}
            for t, s in signal_scores.items()
        }
        run_political_layer(external_signals=_pol_signals)

    # 14. Obsidian export
    logger.info("")
    logger.info("━" * 60)
    logger.info("  OBSIDIAN EXPORT")
    logger.info("━" * 60)
    write_obsidian_notes(
        config=config,
        master_briefing=briefing,
        signal_scores=signal_scores,
        macro_snapshot=macro_snapshot,
        sentiment_results=sentiment_results,
        strategy_results=strategy_results,
        master_portfolio=master,
    )

    # 15. Auto-propose high-conviction trades to the approval queue
    _auto_propose_trades(signal_scores, macro_snapshot, config)

    # 16. Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("  RUN COMPLETE")
    if master:
        logger.info(f"  Combined score:   {master.combined_score:+.1f} / 100")
        logger.info(f"  Est. Sharpe:      {master.estimated_sharpe:.2f}")
        logger.info(f"  Top long:         {master.top_longs[0].ticker if master.top_longs else 'N/A'}")
        logger.info(f"  Top short:        {master.top_shorts[0].ticker if master.top_shorts else 'N/A'}")
        logger.info(f"  Active hedges:    {len(master.active_hedges)}")
        logger.info(f"  1d 99%% VaR:      ${master.portfolio_var:,.0f}")
    logger.info(f"  Strategies run:   {len(strategy_results)}/14")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
