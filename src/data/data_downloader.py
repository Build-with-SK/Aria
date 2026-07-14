"""
data_downloader.py
==================
Handles all market data retrieval. Currently powered by yfinance.

Architecture note: All data access flows through the MarketDataProvider
abstract interface. Swapping to Bloomberg or another vendor later only
requires writing a new provider class — nothing else changes.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf
import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# Abstract interface — every data provider must implement this
# ===========================================================================
class MarketDataProvider(ABC):
    """
    Abstract base class that defines the contract for all data providers.
    Whether the source is yfinance, Bloomberg, or a proprietary feed,
    the rest of the system only ever calls these methods.
    """

    @abstractmethod
    def get_price_data(
        self,
        tickers: List[str],
        period: str = "2y",
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """Return OHLCV DataFrames keyed by ticker."""
        ...

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> Dict:
        """Return key fundamental ratios for a single ticker."""
        ...

    @abstractmethod
    def get_news(self, ticker: str, limit: int = 10) -> List[Dict]:
        """Return recent news headlines for a ticker."""
        ...

    @abstractmethod
    def get_macro_data(self, series_id: str) -> pd.Series:
        """Return a macro time series (e.g. CPI, Fed Funds rate)."""
        ...


# ===========================================================================
# yfinance implementation
# ===========================================================================
class YFinanceProvider(MarketDataProvider):
    """
    Concrete data provider backed by the free yfinance library.
    Suitable for development, research, and backtesting.
    """

    def get_price_data(
        self,
        tickers: List[str],
        period: str = "2y",
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """
        Download OHLCV data for a list of tickers.

        Returns a dict: { "AAPL": DataFrame, "MSFT": DataFrame, ... }
        Each DataFrame has columns: Open, High, Low, Close, Volume
        Index: DatetimeIndex
        """
        results: Dict[str, pd.DataFrame] = {}

        # Download in batches to avoid overwhelming the API
        batch_size = 10
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            logger.info(f"Downloading batch {i//batch_size + 1}: {batch}")

            try:
                raw = yf.download(
                    tickers=batch,
                    period=period,
                    interval=interval,
                    auto_adjust=True,   # Adjusts for splits/dividends
                    progress=False,
                    threads=True,
                )

                if raw.empty:
                    logger.warning(f"No data returned for batch: {batch}")
                    continue

                # yfinance returns multi-level columns when >1 ticker
                if len(batch) == 1:
                    ticker = batch[0]
                    df = raw.copy()
                    df.index = pd.to_datetime(df.index)
                    df = df.dropna(how="all")
                    if not df.empty:
                        results[ticker] = df
                else:
                    for ticker in batch:
                        try:
                            df = raw.xs(ticker, axis=1, level=1).copy()
                            df.index = pd.to_datetime(df.index)
                            df = df.dropna(how="all")
                            if not df.empty:
                                results[ticker] = df
                        except KeyError:
                            logger.warning(f"No data for {ticker}")

            except Exception as e:
                logger.error(f"Download failed for batch {batch}: {e}")

            # Small pause between batches — be respectful of free APIs
            if i + batch_size < len(tickers):
                time.sleep(0.5)

        logger.info(f"Successfully downloaded data for {len(results)} / {len(tickers)} tickers")
        return results

    def get_fundamentals(self, ticker: str) -> Dict:
        """
        Fetch key fundamental ratios from yfinance.
        Returns empty dict if unavailable (common for non-equity assets).
        """
        try:
            info = yf.Ticker(ticker).info
            return {
                "pe_ratio":            info.get("trailingPE"),
                "forward_pe":          info.get("forwardPE"),
                "pb_ratio":            info.get("priceToBook"),
                "ps_ratio":            info.get("priceToSalesTrailing12Months"),
                "market_cap":          info.get("marketCap"),
                "revenue_growth":      info.get("revenueGrowth"),
                "earnings_growth":     info.get("earningsGrowth"),
                "profit_margin":       info.get("profitMargins"),
                "roe":                 info.get("returnOnEquity"),
                "debt_to_equity":      info.get("debtToEquity"),
                "free_cashflow":       info.get("freeCashflow"),
                "dividend_yield":      info.get("dividendYield"),
                "sector":              info.get("sector"),
                "industry":            info.get("industry"),
                "52w_high":            info.get("fiftyTwoWeekHigh"),
                "52w_low":             info.get("fiftyTwoWeekLow"),
                "analyst_target":      info.get("targetMeanPrice"),
            }
        except Exception as e:
            logger.warning(f"Could not fetch fundamentals for {ticker}: {e}")
            return {}

    def get_news(self, ticker: str, limit: int = 10) -> List[Dict]:
        """
        Fetch recent news via yfinance. Returns list of headline dicts.
        Note: yfinance news is limited. Replace with a proper news API in Phase 3.
        """
        try:
            news = yf.Ticker(ticker).news
            if not news:
                return []
            return [
                {
                    "title":     item.get("content", {}).get("title", ""),
                    "publisher": item.get("content", {}).get("provider", {}).get("displayName", ""),
                    "link":      item.get("content", {}).get("canonicalUrl", {}).get("url", ""),
                    "published": item.get("content", {}).get("pubDate", ""),
                    "sentiment": None,  # Populated in Phase 3
                }
                for item in (news[:limit] if news else [])
            ]
        except Exception as e:
            logger.warning(f"Could not fetch news for {ticker}: {e}")
            return []

    def get_macro_data(self, series_id: str) -> pd.Series:
        """
        Placeholder for macro data retrieval.
        In Phase 3 this will call FRED or pandas-datareader.
        series_id examples: 'FEDFUNDS', 'CPIAUCSL', 'T10Y2Y'
        """
        logger.info(f"Macro data for {series_id} not yet implemented — returning empty series")
        return pd.Series(dtype=float, name=series_id)


# ===========================================================================
# Bloomberg provider — architecture placeholder
# ===========================================================================
class BloombergProvider(MarketDataProvider):
    """
    Placeholder for Bloomberg Terminal / BLPAPI integration.
    Implement when Bloomberg API access is available.
    The rest of the system does not change — only this class.
    """

    def __init__(self):
        raise NotImplementedError(
            "Bloomberg integration is not yet implemented. "
            "Set bloomberg.enabled = false in configs/universe.yaml."
        )

    def get_price_data(self, tickers, period="2y", interval="1d"):
        raise NotImplementedError

    def get_fundamentals(self, ticker):
        raise NotImplementedError

    def get_news(self, ticker, limit=10):
        raise NotImplementedError

    def get_macro_data(self, series_id):
        raise NotImplementedError


# ===========================================================================
# Config-driven factory function
# ===========================================================================
def load_config(config_path: str = "configs/universe.yaml") -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_tickers(config: Dict) -> List[str]:
    """
    Extract every ticker symbol from the universe config.
    Returns a flat list of ticker strings.
    """
    tickers = []
    for asset_class, assets in config["universe"].items():
        for asset in assets:
            tickers.append(asset["ticker"])
    return tickers


def get_provider(config: Dict) -> MarketDataProvider:
    """
    Factory: returns the correct data provider based on config.
    Phase 1 always returns YFinanceProvider.
    """
    if config.get("bloomberg", {}).get("enabled", False):
        return BloombergProvider()
    return YFinanceProvider()


def build_ticker_metadata(config: Dict) -> Dict[str, Dict]:
    """
    Build a lookup dict: { "AAPL": {"name": "Apple", "class": "equities", ...} }
    Useful for labels, logging, and the dashboard.
    """
    meta = {}
    for asset_class, assets in config["universe"].items():
        for asset in assets:
            ticker = asset["ticker"]
            meta[ticker] = {**asset, "asset_class": asset_class}
    return meta


# ===========================================================================
# Save / load helpers
# ===========================================================================
def save_data(data: Dict[str, pd.DataFrame], output_dir: str = "data/raw") -> None:
    """Save all DataFrames to CSV files in output_dir."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for ticker, df in data.items():
        # Replace special characters so the filename is safe on all OS
        safe_name = ticker.replace("=", "_").replace("^", "_").replace("-", "_")
        path = Path(output_dir) / f"{safe_name}.csv"
        df.to_csv(path)
    logger.info(f"Saved {len(data)} files to {output_dir}")


def load_data(data_dir: str = "data/raw") -> Dict[str, pd.DataFrame]:
    """Load previously saved CSVs back into memory."""
    data = {}
    for csv_file in Path(data_dir).glob("*.csv"):
        df = pd.read_csv(csv_file, index_col=0, parse_dates=True)
        # Reverse the filename sanitisation to recover the original ticker
        ticker = csv_file.stem.replace("_", "=", 1)  # Best-effort
        data[ticker] = df
    return data


# ===========================================================================
# Quick test — run this file directly to verify downloads are working
# ===========================================================================
if __name__ == "__main__":
    cfg = load_config("configs/universe.yaml")
    provider = get_provider(cfg)
    tickers = get_all_tickers(cfg)
    print(f"Downloading {len(tickers)} assets...")
    data = provider.get_price_data(tickers, period="6mo")
    save_data(data)
    print("\nSample — AAPL last 5 rows:")
    if "AAPL" in data:
        print(data["AAPL"].tail())
