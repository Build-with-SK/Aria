"""
src/research/sources/markets.py
===============================
The two public endpoints with the highest signal-per-request for a trading
system: StockTwits and SEC EDGAR full-text search.

StockTwits is retail positioning talk, tagged by ticker, with an explicit
bullish/bearish label on many messages. Its public streams API needs no key.
Treat it as a crowd-positioning gauge, never as fact — it is the loudest
corner of the retail internet and it is wrong often and confidently.

EDGAR full-text search is the opposite: primary-source filings, official,
free, and authoritative. A phrase search across every 8-K and 10-Q is the
single best lead generator in this module, and nothing about it can be
called scraping. SEC asks for a descriptive User-Agent, which http.py sends.
"""
from __future__ import annotations

from ..base import Document, Source, SourceError
from ..http import get_json, q
from ..url import host_matches, normalize_public_url

STOCKTWITS = "https://api.stocktwits.com/api/2"
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index?q="
EDGAR_UI = "https://www.sec.gov/cgi-bin/browse-edgar"


class StockTwitsSource(Source):
    name = "stocktwits"
    description = "StockTwits ticker streams — retail sentiment, public API"
    backends = ["public-api"]
    zero_config = True

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "stocktwits.com")

    def fetch(self, url: str) -> Document:
        clean = normalize_public_url(url)
        parts = [p for p in clean.split("/") if p]
        symbol = parts[-1] if parts else ""
        if not symbol:
            raise SourceError(f"no symbol in {clean}")
        return self.symbol(symbol.upper())

    def symbol(self, ticker: str, limit: int = 30) -> Document:
        """Recent messages for one ticker, with the bull/bear tally."""
        payload = get_json(f"{STOCKTWITS}/streams/symbol/{ticker}.json?limit={limit}")
        messages = payload.get("messages", []) or []

        bulls = bears = 0
        lines = []
        for msg in messages:
            sentiment = ((msg.get("entities") or {}).get("sentiment") or {}).get("basic", "")
            if sentiment == "Bullish":
                bulls += 1
            elif sentiment == "Bearish":
                bears += 1
            user = (msg.get("user") or {}).get("username", "?")
            tag = f"[{sentiment}] " if sentiment else ""
            lines.append(f"- {tag}**{user}**: {(msg.get('body') or '').strip()}")

        labelled = bulls + bears
        return Document(
            url=f"https://stocktwits.com/symbol/{ticker}",
            title=f"StockTwits ${ticker}: {bulls} bullish / {bears} bearish",
            text="\n".join(lines),
            source=self.name,
            backend="public-api",
            meta={
                "ticker": ticker,
                "messages": len(messages),
                "bullish": bulls,
                "bearish": bears,
                # None, not 0.5, when nobody tagged: an absent reading and a
                # balanced one are different facts and must not look alike.
                "bull_ratio": round(bulls / labelled, 3) if labelled else None,
            },
        )


class EdgarSource(Source):
    name = "edgar"
    description = "SEC EDGAR full-text search over all filings (official, free)"
    backends = ["efts"]
    zero_config = True

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "sec.gov", "efts.sec.gov")

    def fetch(self, url: str) -> Document:
        from .web import WebSource
        doc = WebSource().fetch(url)      # a filing page reads fine as markdown
        doc.source = self.name
        return doc

    def search(self, query: str, limit: int = 25, forms: str | None = None,
               date_from: str | None = None, date_to: str | None = None) -> list[Document]:
        """Full-text search every filing. `forms` e.g. "8-K" or "10-Q,10-K".

        Dates are YYYY-MM-DD. Quote the query for a phrase match.

        EDGAR ranks by relevance, which for a common phrase happily returns a
        2002 filing first. Results are re-sorted newest-first here, but that
        only reorders the hits relevance already chose — pass `date_from` when
        recency actually matters.
        """
        params = q(**{"q": query, "forms": forms, "dateRange": "custom" if date_from else None,
                      "startdt": date_from, "enddt": date_to})
        payload = get_json(f"https://efts.sec.gov/LATEST/search-index?{params}")
        hits = ((payload.get("hits") or {}).get("hits") or [])[:limit]

        out = []
        for hit in hits:
            src = hit.get("_source", {}) or {}
            # _id is "<accession-no-dashes>:<primary-doc>"; both halves are
            # needed to build a link straight to the document itself.
            ident = hit.get("_id", "")
            accession, _, doc_name = ident.partition(":")
            cik = (src.get("ciks") or [""])[0].lstrip("0")
            link = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{accession.replace('-', '')}/{doc_name}"
                if cik and accession else "https://www.sec.gov/edgar/search/"
            )
            names = src.get("display_names") or []
            out.append(Document(
                url=link,
                title=f"{src.get('file_type', '')} — {names[0] if names else ''}",
                text=src.get("file_description", "") or "",
                source=self.name,
                backend="efts",
                meta={
                    "form": src.get("file_type", ""),
                    "filed": src.get("file_date", ""),
                    "cik": cik,
                    "company": names[0] if names else "",
                    "accession": accession,
                },
            ))
        return sorted(out, key=lambda d: d.meta.get("filed", ""), reverse=True)
