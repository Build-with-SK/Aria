"""
main.py — Phase 4 orchestrator (all phases preserved)
Run: python main.py                     # Full run
     python main.py --spot-only         # Phase 1 only
     python main.py --no-sentiment      # Skip news sentiment
     python main.py --no-db             # Skip database writes
"""
from __future__ import annotations
import argparse, json, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Phase 1
from src.data.data_downloader import get_all_tickers, get_provider, build_ticker_metadata, load_config, save_data
from src.features.indicators import build_all_features
from src.signals.signal_engine import build_all_signals, get_top_signals

# Phase 2
from src.derivatives.futures_analyzer import analyze_all_futures, futures_to_json
from src.derivatives.options_analyzer import analyze_all_options, options_to_json
from src.derivatives.derivatives_signal_engine import build_all_derivatives_contexts, derivatives_context_to_json
from src.models.model_trainer import train_all_models
from src.models.prediction_engine import predict_all_assets, predictions_to_json
from src.backtesting.backtester import run_all_backtests, backtests_to_json
from src.portfolio.portfolio_analyzer import run_portfolio_analysis
from src.reports.report_generator import generate_report, print_report, save_report

# Phase 3
from src.macro.macro_data import fetch_macro_snapshot, macro_to_json
from src.sentiment.sentiment_analyzer import analyze_all_sentiment, sentiment_to_json
from src.alerts.alerts_engine import generate_all_alerts, alerts_to_json, save_alerts

# Phase 4
from src.database.database import (
    initialise_database, save_signals, save_prices, save_backtest_results,
    save_alerts as db_save_alerts, save_macro, save_ml_predictions,
    update_signal_performance, get_database_stats,
)
from src.portfolio.portfolio_optimizer import optimise_portfolio, portfolio_to_json
from src.notifications.notifications import (
    send_notifications, load_notification_config, create_default_notification_config,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("main")


def signals_to_json_dict(signals) -> dict:
    out = {}
    for ticker, sig in signals.items():
        out[ticker] = {
            "ticker": sig.ticker, "name": sig.name, "asset_class": sig.asset_class,
            "current_price": sig.current_price, "composite_score": sig.composite_score,
            "action": sig.action, "confidence": sig.confidence,
            "bullish_prob": sig.bullish_prob, "bearish_prob": sig.bearish_prob,
            "trend_score": sig.trend_score, "momentum_score": sig.momentum_score,
            "volatility_score": sig.volatility_score, "regime_score": sig.regime_score,
            "macro_score": sig.macro_score, "sentiment_score": sig.sentiment_score,
            "risk_level": sig.risk_level, "realised_vol": sig.realised_vol,
            "atr_pct": sig.atr_pct, "stop_loss": sig.stop_loss,
            "take_profit": sig.take_profit, "invalidation": sig.invalidation,
            "position_size_pct": sig.position_size_pct, "regime": sig.regime,
            "drivers": sig.drivers, "risks": sig.risks, "explanation": sig.explanation,
            "price_52w_high": sig.price_52w_high, "price_52w_low": sig.price_52w_low,
        }
    return out


def _save(data, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"Saved → {path}")


def run(spot_only=False, no_sentiment=False, no_db=False):
    logger.info("=" * 60)
    logger.info("Trading Intelligence System — Phase 4")
    logger.info("=" * 60)

    cfg = load_config("configs/universe.yaml")
    p3  = cfg.get("phase3", {})
    p4  = cfg.get("phase4", {})
    risk_free = cfg["system"].get("risk_free_rate", 0.045)


# ── Obsidian Export ───────────────────────────────────────────────────────
    try:
        from src.obsidian.obsidian_exporter import ObsidianExporter, ObsidianVaultError
        exporter = ObsidianExporter("configs/obsidian.yaml")
        exporter.export_all(
            signals_dict     = signals_dict,
            backtest_results = bt_json                              if backtest_results else {},
            ml_predictions   = predictions_to_json(ml_predictions)  if ml_predictions  else {},
            macro_data       = macro_data,
            sentiment_data   = sentiment_data,
            alerts_json      = alerts_json_list,
            portfolio_data   = portfolio_data,
            opt_results      = opt_results,
            futures_data     = futures_to_json(futures_signals)     if futures_signals  else {},
            options_data     = options_to_json(options_data)        if options_data     else {},
            report           = report,
        )
    except ObsidianVaultError as e:
        logger.error(f"Obsidian vault error: {e}")
    except Exception as e:
        logger.warning(f"Obsidian export failed (non-fatal): {e}")


    # ── Phase 4: Init database ────────────────────────────────────────────────
    if not no_db:
        try:
            initialise_database()
        except Exception as e:
            logger.error(f"Database init failed: {e}")

    # Create notification config template if missing
    create_default_notification_config()

    # ── Phase 1: Spot pipeline ────────────────────────────────────────────────
    provider = get_provider(cfg)
    tickers  = get_all_tickers(cfg)
    metadata = build_ticker_metadata(cfg)

    logger.info(f"Downloading data for {len(tickers)} assets...")
    raw_data = provider.get_price_data(tickers, period=cfg["system"]["default_period"], interval=cfg["system"]["default_interval"])
    save_data(raw_data, output_dir="data/raw")

    logger.info("Building features...")
    featured_data = build_all_features(raw_data, cfg, benchmark_ticker=cfg["system"]["benchmark"])

    logger.info("Generating spot signals...")
    signals = build_all_signals(featured_data, metadata)
    signals_dict = signals_to_json_dict(signals)
    _save(signals_dict, "data/signals.json")

    # Save prices and signals to DB
    if not no_db:
        try:
            save_prices(raw_data)
            save_signals(signals_dict)
        except Exception as e:
            logger.error(f"DB price/signal save failed: {e}")

    top_buy  = get_top_signals(signals, n=5, direction="bullish")
    top_sell = get_top_signals(signals, n=5, direction="bearish")
    print("\n" + "=" * 60)
    print("TOP BULLISH SIGNALS")
    print("=" * 60)
    for sig in top_buy:
        print(f"  {sig.ticker:<12} {sig.action:<14} Score: {sig.composite_score:>+7.1f}  Conf: {sig.confidence:<6}  Vol: {sig.realised_vol:.0%}")
    print("\n" + "=" * 60)
    print("TOP BEARISH SIGNALS")
    print("=" * 60)
    for sig in top_sell:
        print(f"  {sig.ticker:<12} {sig.action:<14} Score: {sig.composite_score:>+7.1f}  Conf: {sig.confidence:<6}  Vol: {sig.realised_vol:.0%}")

    if spot_only:
        print("\nSpot-only mode. Dashboard: streamlit run src/dashboard/dashboard.py")
        return

    # ── Phase 2: Derivatives ──────────────────────────────────────────────────
    futures_signals = {}
    try:
        logger.info("Analysing futures...")
        futures_signals = analyze_all_futures(cfg, raw_spot_data=raw_data)
        _save(futures_to_json(futures_signals), "data/futures_signals.json")
    except Exception as e:
        logger.error(f"Futures failed: {e}")

    options_data = {}
    try:
        logger.info("Analysing options...")
        options_data = analyze_all_options(cfg, risk_free_rate=risk_free)
        _save(options_to_json(options_data), "data/options_data.json")
    except Exception as e:
        logger.error(f"Options failed: {e}")

    try:
        deriv_ctx = build_all_derivatives_contexts(list(signals.keys()), futures_signals, options_data)
        _save(derivatives_context_to_json(deriv_ctx), "data/derivatives_context.json")
    except Exception as e:
        logger.error(f"Derivatives context failed: {e}")

    # ── Phase 2: ML ───────────────────────────────────────────────────────────
    try:
        logger.info("Training ML models...")
        train_all_models(featured_data, cfg)
    except Exception as e:
        logger.error(f"ML training failed: {e}")

    ml_predictions = {}
    try:
        logger.info("Generating ML predictions...")
        ml_predictions = predict_all_assets(featured_data, cfg)
        _save(predictions_to_json(ml_predictions), "data/ml_predictions.json")
        if not no_db:
            save_ml_predictions(predictions_to_json(ml_predictions))
    except Exception as e:
        logger.error(f"ML predictions failed: {e}")

    # ── Phase 2: Backtesting ──────────────────────────────────────────────────
    backtest_results = {}
    try:
        logger.info("Running backtests...")
        backtest_results = run_all_backtests(featured_data, cfg)
        bt_json = backtests_to_json(backtest_results)
        _save(bt_json, "data/backtest_results.json")
        if not no_db:
            save_backtest_results(bt_json)
    except Exception as e:
        logger.error(f"Backtesting failed: {e}")

    # ── Phase 2: Portfolio analysis ───────────────────────────────────────────
    portfolio_data = {}
    try:
        logger.info("Portfolio analysis...")
        portfolio_data = run_portfolio_analysis(signals_dict, featured_data)
        _save(portfolio_data, "data/portfolio_analysis.json")
    except Exception as e:
        logger.error(f"Portfolio analysis failed: {e}")

    # ── Phase 3: Macro ────────────────────────────────────────────────────────
    macro_data = {}
    if p3.get("run_macro", True):
        try:
            logger.info("Fetching macro data...")
            macro_snap = fetch_macro_snapshot()
            macro_data = macro_to_json(macro_snap)
            _save(macro_data, "data/macro_data.json")
            if not no_db:
                save_macro(macro_data)
            logger.info(f"Macro: regime={macro_snap.regime}, score={macro_snap.macro_score}, VIX={macro_snap.vix}")
        except Exception as e:
            logger.error(f"Macro failed: {e}")

    # ── Phase 3: Sentiment ────────────────────────────────────────────────────
    sentiment_data = {}
    if p3.get("run_sentiment", True) and not no_sentiment:
        try:
            logger.info("Analysing news sentiment...")
            sentiment_results = analyze_all_sentiment(cfg)
            sentiment_data = sentiment_to_json(sentiment_results)
            _save(sentiment_data, "data/sentiment_data.json")
        except Exception as e:
            logger.error(f"Sentiment failed: {e}")
    elif no_sentiment:
        logger.info("Sentiment skipped (--no-sentiment)")

    # ── Phase 3: Alerts ───────────────────────────────────────────────────────
    alerts = []
    alerts_json_list = []
    try:
        logger.info("Generating alerts...")
        alerts = generate_all_alerts(signals_dict, macro_data, sentiment_data, portfolio_data)
        alerts_json_list = alerts_to_json(alerts)
        save_alerts(alerts)
        if not no_db:
            db_save_alerts(alerts_json_list)
        crit = sum(1 for a in alerts if a.severity == "CRITICAL")
        warn = sum(1 for a in alerts if a.severity == "WARNING")
        logger.info(f"Alerts: {crit} critical, {warn} warning, {len(alerts)-crit-warn} info")
    except Exception as e:
        logger.error(f"Alerts failed: {e}")

    # ── Phase 4: Portfolio optimisation ───────────────────────────────────────
    opt_results = {}
    try:
        logger.info("Running portfolio optimisation (3 strategies)...")
        strategies = p4.get("optimisation_strategies", ["signal_weighted", "risk_parity", "mean_variance"])
        for strat in strategies:
            opt = optimise_portfolio(signals_dict, featured_data, strategy=strat,
                                     risk_free=risk_free,
                                     max_weight=p4.get("max_weight", 0.15),
                                     total_capital=p4.get("portfolio_value", 100_000))
            opt_results[strat] = portfolio_to_json(opt)
            logger.info(f"  {strat}: Sharpe={opt.sharpe_ratio:.2f}, "
                        f"allocated={opt.total_allocated:.1%}, "
                        f"div={opt.diversification:.0f}/100")
        _save(opt_results, "data/optimised_portfolios.json")
    except Exception as e:
        logger.error(f"Portfolio optimisation failed: {e}")

    # ── Phase 4: Signal performance update ────────────────────────────────────
    if not no_db:
        try:
            logger.info("Updating historical signal performance...")
            update_signal_performance(raw_data)
        except Exception as e:
            logger.error(f"Signal performance update failed: {e}")

    # ── Phase 4: Send notifications ───────────────────────────────────────────
    try:
        notif_cfg = load_notification_config()
        report_for_notif = {"market_regime": "See dashboard", "regime_stats": {}}
        result = send_notifications(alerts_json_list, report_for_notif, notif_cfg)
        if result.get("email") or result.get("telegram"):
            logger.info(f"Notifications sent: {result}")
    except Exception as e:
        logger.error(f"Notifications failed: {e}")

    # ── Daily report ──────────────────────────────────────────────────────────
    try:
        logger.info("Generating daily report...")
        report = generate_report(
            signals=signals_dict,
            futures_data=futures_to_json(futures_signals) if futures_signals else None,
            options_data=options_to_json(options_data) if options_data else None,
            ml_predictions=predictions_to_json(ml_predictions) if ml_predictions else None,
            portfolio_data=portfolio_data if portfolio_data else None,
        )
        report["macro"] = macro_data
        report["sentiment_summary"] = {
            "bullish": sum(1 for s in sentiment_data.values() if s.get("label") == "Bullish"),
            "bearish": sum(1 for s in sentiment_data.values() if s.get("label") == "Bearish"),
            "neutral": sum(1 for s in sentiment_data.values() if s.get("label") == "Neutral"),
        }
        report["alerts_count"] = {
            "critical": sum(1 for a in alerts if a.severity == "CRITICAL"),
            "warning":  sum(1 for a in alerts if a.severity == "WARNING"),
            "info":     sum(1 for a in alerts if a.severity == "INFO"),
        }
        report["optimised_portfolios"] = list(opt_results.keys())
        _save(report, "data/daily_report.json")
        save_report(report)
        print_report(report)
    except Exception as e:
        logger.error(f"Report failed: {e}")

    # ── Phase 4: Database stats ───────────────────────────────────────────────
    if not no_db:
        try:
            stats = get_database_stats()
            logger.info(f"Database: {stats.get('signals_history',0)} signals, "
                        f"{stats.get('price_history',0)} prices, "
                        f"{stats.get('alerts_history',0)} alerts stored")
        except Exception as e:
            logger.debug(f"DB stats failed: {e}")

    # ── Phase 4: Obsidian + Pulse Export ─────────────────────────────────────
    try:
        from src.obsidian.obsidian_exporter import ObsidianExporter
        exporter = ObsidianExporter(config_path="configs/obsidian.yaml")
        exporter.export_all()
        logger.info("Obsidian export completed successfully.")
    except FileNotFoundError as e:
        logger.warning(f"Obsidian vault not found — skipping export. ({e})")
    except Exception as e:
        logger.warning(f"Obsidian export skipped: {e}")

    print("\n" + "=" * 60)
    print("Phase 4 complete.")
    print("Dashboard:  streamlit run src/dashboard/dashboard.py")
    print("Scheduler:  python src/scheduler/scheduler.py --install")
    print("Obsidian:   Check your vault for exported notes")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading Intelligence System — Phase 4")
    parser.add_argument("--spot-only",    action="store_true", help="Phase 1 only (fast)")
    parser.add_argument("--no-sentiment", action="store_true", help="Skip news sentiment")
    parser.add_argument("--no-db",        action="store_true", help="Skip database writes")
    args = parser.parse_args()
    run(spot_only=args.spot_only, no_sentiment=args.no_sentiment, no_db=args.no_db)
