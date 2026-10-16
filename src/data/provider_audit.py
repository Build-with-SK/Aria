"""Catalogue-wide structural checks and conservative same-listing BSE repairs.

A structural pass is not a live coverage test. Numeric BSE mappings are review
candidates, not proven errors. Verification never guesses another venue.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re


def bse_candidate(row: dict) -> str | None:
    display = str(row.get("symbol") or "").upper()
    provider = str(row.get("yahoo") or "").upper()
    if (row.get("exchange") == "BSE" and row.get("asset_class") == "equity"
            and re.fullmatch(r"[A-Z0-9&._-]+\.BO", display)
            and re.fullmatch(r"\d+\.BO", provider) and display != provider):
        return display
    return None


def catalogue_audit(rows: list[dict]) -> dict:
    """Inspect every row without network requests or writes."""
    records = []
    for row in rows:
        issues = []
        for field in ("symbol", "yahoo", "exchange", "asset_class", "currency"):
            if not row.get(field):
                issues.append(f"missing_{field}")
        candidate = bse_candidate(row)
        if candidate:
            issues.append("bse_numeric_provider_needs_live_check")
        records.append({"symbol": row.get("symbol"), "provider_symbol": row.get("yahoo"),
                        "exchange": row.get("exchange"), "asset_class": row.get("asset_class"),
                        "currency": row.get("currency"), "issues": issues,
                        "same_listing_candidate": candidate, "live_status": "not_checked"})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "total": len(rows),
            "by_exchange": dict(Counter(row.get("exchange") for row in rows)),
            "by_asset_class": dict(Counter(row.get("asset_class") for row in rows)),
            "issue_counts": dict(Counter(issue for row in records for issue in row["issues"])),
            "live_checked": 0, "records": records}


def _company(name: str) -> str:
    # Deliberately narrow: do not drop INDIA, HOLDINGS, industries or other
    # meaningful words. Short/truncated provider names are insufficient.
    words = re.findall(r"[A-Z0-9]+", str(name or "").upper())
    return " ".join("LIMITED" if word == "LTD" else word for word in words)


def verify_bse_candidate(row: dict, ticker_factory=None) -> dict:
    """Return evidence only. This function never changes a catalogue row.

    Require OHLC, exact provider ticker, BSE, INR and the same full company
    name (or matching ISIN). A feed can exist without sufficient analysis
    history; `bars` is evidence of availability, not a nine-frame promise.
    """
    candidate = bse_candidate(row)
    result = {"symbol": row.get("symbol"), "old_provider": row.get("yahoo"),
              "candidate": candidate, "verified": False,
              "checked_at": datetime.now(timezone.utc).isoformat()}
    if not candidate:
        return {**result, "reason": "no supported same-listing repair candidate"}
    if ticker_factory is None:
        import yfinance as yf
        ticker_factory = yf.Ticker
    try:
        ticker = ticker_factory(candidate)
        bars = ticker.history(period="1mo", interval="1d", auto_adjust=False)
        if bars is None or bars.empty or not {"Open", "High", "Low", "Close"}.issubset(bars.columns):
            return {**result, "reason": "candidate has no OHLC history"}
        usable = bars[["Open", "High", "Low", "Close"]].dropna()
        if usable.empty or (usable <= 0).any().any():
            return {**result, "reason": "candidate has invalid OHLC history"}
        metadata = ticker.get_history_metadata() or {}
        name = metadata.get("longName") or ""
        # Chart metadata supplies the exchange and currency. Fetch a full name
        # only when it is absent; no truncated-name fuzzy matching.
        if not name:
            info = ticker.get_info() or {}
            name = info.get("longName") or ""
            metadata = {**info, **metadata}
        result.update(provider_symbol=metadata.get("symbol"), exchange=metadata.get("exchangeName") or metadata.get("exchange"),
                      currency=metadata.get("currency"), name=name, bars=len(usable),
                      data_as_of=str(usable.index[-1]), isin=row.get("isin"))
        if str(result["provider_symbol"] or "").upper() != candidate:
            return {**result, "reason": "provider ticker mismatch"}
        if str(result["exchange"] or "").upper() != "BSE" or result["currency"] != "INR":
            return {**result, "reason": "venue or currency mismatch"}
        provider_isin = metadata.get("isin")
        if provider_isin and row.get("isin") and provider_isin != row["isin"]:
            return {**result, "reason": "ISIN mismatch"}
        same_isin = provider_isin and provider_isin == row.get("isin")
        if not same_isin and (not name or not row.get("name") or _company(name) != _company(row["name"])):
            return {**result, "reason": "full company name could not be verified"}
        return {**result, "verified": True, "reason": "same BSE listing verified"}
    except Exception as exc:
        return {**result, "reason": f"provider unavailable: {type(exc).__name__}"}
