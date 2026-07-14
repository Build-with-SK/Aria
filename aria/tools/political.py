"""
ARIA Tool: get_political_data
Congressional trading activity and political intelligence for a ticker.
Uses Quiver Quantitative public data where available.
"""

import asyncio
from datetime import datetime


async def get_political_data(ticker: str) -> dict:
    """
    Fetch congressional trading data for a ticker.
    Primary source: Quiver Quantitative (free tier).
    """
    try:
        import httpx
        url = f"https://api.quiverquant.com/beta/live/congresstrading/{ticker}"
        headers = {"Accept": "application/json"}

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                return _process_quiver_data(ticker, data)
            else:
                return _fallback_response(ticker, f"Quiver API returned {r.status_code}")

    except Exception as e:
        return _fallback_response(ticker, str(e))


def _process_quiver_data(ticker: str, raw: list) -> dict:
    """Process congressional trade records."""
    if not raw:
        return {
            "ticker": ticker,
            "status": "no_data",
            "message": "No congressional trades found for this ticker in the public dataset.",
            "trades": [],
        }

    trades = []
    buy_count = sell_count = 0

    for record in raw[:20]:  # latest 20
        trade_type = record.get("Transaction", "").lower()
        if "purchase" in trade_type or "buy" in trade_type:
            buy_count += 1
            direction = "buy"
        elif "sale" in trade_type or "sell" in trade_type:
            sell_count += 1
            direction = "sell"
        else:
            direction = "other"

        trades.append({
            "representative": record.get("Representative", "Unknown"),
            "party": record.get("Party", "Unknown"),
            "transaction_date": record.get("TransactionDate", ""),
            "trade_date": record.get("TransactionDate", ""),
            "amount_range": record.get("Range", "Unknown"),
            "direction": direction,
            "description": record.get("Transaction", ""),
        })

    # Sentiment
    total = buy_count + sell_count
    if total == 0:
        congress_sentiment = "neutral"
    elif buy_count / total > 0.65:
        congress_sentiment = "bullish"
    elif sell_count / total > 0.65:
        congress_sentiment = "bearish"
    else:
        congress_sentiment = "mixed"

    return {
        "ticker": ticker,
        "status": "ok",
        "total_records": len(raw),
        "trades": trades,
        "summary": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "congressional_sentiment": congress_sentiment,
        },
        "interpretation": (
            f"Congressional members have executed {buy_count} buys and {sell_count} sells recently. "
            f"Overall congressional sentiment: {congress_sentiment}. "
            "Note: congressional trades are disclosed with a delay and are for informational purposes only."
        ),
        "source": "Quiver Quantitative",
        "timestamp": datetime.now().isoformat(),
    }


def _fallback_response(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker,
        "status": "unavailable",
        "reason": reason,
        "message": (
            "Congressional trading data is temporarily unavailable. "
            "You can check manually at: https://www.quiverquant.com/congresstrading/ "
            "or https://efts.sec.gov/LATEST/search-index?q=%22" + ticker + "%22&dateRange=custom"
        ),
    }
