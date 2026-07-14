"""
propublica.py
=============
ProPublica Congress API integration.

What this provides:
  - Current senator and representative roster with party affiliation
  - Committee assignments for every member of Congress
  - Used to ENRICH political records with real committee data
    (improves committee_relevance_score accuracy)

NOT used for stock trade data — ProPublica's Congress API
covers member info, votes, bills, and sponsorship. Actual
trade disclosures come from Senate eFTS and House portals.

API key:
  Free. Register at: https://www.propublica.org/datastore/api/propublica-congress-api
  Takes about 60 seconds to get a key via email.
  Set it in: configs/political_sources.yaml → propublica.api_key

Rate limit: 5,000 requests/day (more than enough).

Congress number:
  119th Congress = January 2025 – January 2027
  118th Congress = January 2023 – January 2025
  Update CURRENT_CONGRESS when a new Congress begins.

Trading Intelligence System — Phase 4/5
Research and educational purposes only.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CACHE_DIR = BASE_DIR / "data" / "political" / "raw" / "propublica_cache"
ROSTER_CACHE = BASE_DIR / "data" / "political" / "raw" / "senate_cache" / "senator_roster.json"
COMMITTEE_CACHE = CACHE_DIR / "committees.json"

# 119th Congress (2025–2027). Update when 120th begins Jan 2027.
CURRENT_CONGRESS = 119

BASE_URL = "https://api.propublica.org/congress/v1"
REQUEST_DELAY = 0.5   # seconds between requests
TIMEOUT = 10

# How long (days) before re-fetching the roster (members don't change often)
ROSTER_MAX_AGE_DAYS = 7


# ─────────────────────────────────────────────
#  Load / save cache
# ─────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _cache_is_fresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return False
    age = datetime.utcnow().timestamp() - path.stat().st_mtime
    return age < (max_age_days * 86400)


# ─────────────────────────────────────────────
#  ProPublica API client
# ─────────────────────────────────────────────

class ProPublicaClient:
    """
    Thin client for the ProPublica Congress API.

    Get your free API key at:
      https://www.propublica.org/datastore/api/propublica-congress-api
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session() if _REQUESTS_OK else None
        if self.session:
            self.session.headers.update({
                "X-API-Key": api_key,
                "User-Agent": "TradingIntelligenceSystem/4.0 (educational research)",
                "Accept": "application/json",
            })

    def _get(self, endpoint: str) -> Optional[dict]:
        if not _REQUESTS_OK or not self.session:
            return None
        url = f"{BASE_URL}/{endpoint}"
        try:
            time.sleep(REQUEST_DELAY)
            resp = self.session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("ProPublica: Invalid API key. "
                             "Get a free key at propublica.org/datastore/api/propublica-congress-api")
            elif e.response.status_code == 429:
                logger.warning("ProPublica: Rate limit hit. Waiting 60s...")
                time.sleep(60)
            else:
                logger.error(f"ProPublica HTTP error: {e.response.status_code} for {endpoint}")
            return None
        except Exception as e:
            logger.error(f"ProPublica request failed ({endpoint}): {e}")
            return None

    def get_members(self, chamber: str, congress: int = CURRENT_CONGRESS) -> List[dict]:
        """Get all members of the given chamber ('senate' or 'house')."""
        data = self._get(f"{congress}/{chamber}/members.json")
        if not data:
            return []
        try:
            return data.get("results", [{}])[0].get("members", [])
        except (IndexError, KeyError, TypeError):
            return []

    def get_member_detail(self, member_id: str) -> Optional[dict]:
        """Get detailed info for a specific member, including committee assignments."""
        data = self._get(f"members/{member_id}.json")
        if not data:
            return None
        try:
            results = data.get("results", [])
            return results[0] if results else None
        except (IndexError, TypeError):
            return None


# ─────────────────────────────────────────────
#  Roster builder
# ─────────────────────────────────────────────

def _build_roster_entry(member: dict) -> dict:
    """Extract key fields from a ProPublica member object."""
    return {
        "id": member.get("id", ""),
        "first_name": member.get("first_name", ""),
        "last_name": member.get("last_name", ""),
        "full_name": f"{member.get('first_name', '')} {member.get('last_name', '')}".strip(),
        "party": member.get("party", ""),    # "D", "R", "I"
        "state": member.get("state", ""),
        "chamber": member.get("chamber", ""),
        "title": member.get("title", ""),
        "seniority": member.get("seniority", ""),
        "leadership_role": member.get("leadership_role", ""),
        "committees": [],  # populated by _enrich_with_committees
    }


def _party_full(party_code: str) -> str:
    return {"D": "Democrat", "R": "Republican", "I": "Independent"}.get(
        party_code.upper(), party_code
    )


# ─────────────────────────────────────────────
#  Main fetcher class
# ─────────────────────────────────────────────

class ProPublicaFetcher:
    """
    Fetches member roster and committee data from ProPublica Congress API.

    This data ENRICHES political trade records:
      - Adds real party affiliation (not guessed)
      - Adds committee assignments (improves committee_relevance_score)
      - Adds seniority and leadership role (improves influence_score)

    Does NOT fetch stock trade data — that comes from Senate eFTS
    and House disclosure portals.

    Usage:
        fetcher = ProPublicaFetcher(api_key="your_key_here")
        roster = fetcher.get_full_roster()     # {last_state_key: member_dict}
        committees = fetcher.get_committees()   # {member_id: [committee_names]}
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ""
        self._client = ProPublicaClient(self.api_key) if self.api_key else None

    def available(self) -> bool:
        return bool(_REQUESTS_OK and self.api_key)

    def get_full_roster(self, force_refresh: bool = False) -> Dict[str, dict]:
        """
        Fetch the full Congress member roster (both chambers).

        Returns dict keyed by "lastname_state" (lowercase) for fast lookup
        in the Senate eFTS parser.

        Cached for ROSTER_MAX_AGE_DAYS days to avoid redundant API calls.
        """
        if not force_refresh and _cache_is_fresh(ROSTER_CACHE, ROSTER_MAX_AGE_DAYS):
            logger.info("ProPublica: using cached roster")
            return _load_json(ROSTER_CACHE)

        if not self.available():
            logger.info(
                "ProPublica: no API key configured — skipping roster fetch. "
                "Set propublica.api_key in configs/political_sources.yaml. "
                "Get a free key at: propublica.org/datastore/api/propublica-congress-api"
            )
            return {}

        logger.info("ProPublica: fetching current Congress member roster...")
        roster: Dict[str, dict] = {}

        for chamber in ("senate", "house"):
            members = self._client.get_members(chamber)
            logger.info(f"  ProPublica: {len(members)} {chamber} members retrieved")

            for m in members:
                entry = _build_roster_entry(m)
                entry["party_full"] = _party_full(entry["party"])
                entry["chamber"] = chamber

                # Build lookup key: "lastname_state" (what Senate eFTS parser uses)
                key = f"{entry['last_name'].lower()}_{entry['state'].lower()}"
                roster[key] = entry

                # Also key by member ID for committee lookup
                if entry["id"]:
                    roster[entry["id"]] = entry

            time.sleep(REQUEST_DELAY)

        if roster:
            _save_json(ROSTER_CACHE, roster)
            logger.info(f"ProPublica: roster saved ({len(roster)} entries)")

        return roster

    def enrich_records_with_committees(
        self,
        roster: Dict[str, dict],
        max_members: int = 50,
    ) -> Dict[str, List[str]]:
        """
        Fetch committee assignments for members in the roster.

        Returns dict: {member_id: [committee_name_1, committee_name_2, ...]}

        Only fetches the first max_members members to stay within rate limits.
        Increases over time as the cache builds up.
        """
        if not self.available():
            return {}

        if _cache_is_fresh(COMMITTEE_CACHE, ROSTER_MAX_AGE_DAYS):
            logger.info("ProPublica: using cached committee data")
            return _load_json(COMMITTEE_CACHE)

        logger.info(f"ProPublica: fetching committee assignments (up to {max_members} members)...")
        committee_map: Dict[str, List[str]] = {}

        # Get unique member IDs from roster (avoid duplicates from dual-keying)
        seen_ids = set()
        count = 0
        for key, member in roster.items():
            member_id = member.get("id", "")
            if not member_id or member_id in seen_ids:
                continue
            seen_ids.add(member_id)

            if count >= max_members:
                break

            detail = self._client.get_member_detail(member_id)
            if detail:
                roles = detail.get("roles", [])
                if roles:
                    # Most recent role is first
                    committees = roles[0].get("committees", [])
                    committee_names = [c.get("name", "") for c in committees if c.get("name")]
                    if committee_names:
                        committee_map[member_id] = committee_names
                        logger.debug(f"  {member.get('full_name', '?')}: {len(committee_names)} committees")

            count += 1

        if committee_map:
            _save_json(COMMITTEE_CACHE, committee_map)
            logger.info(f"ProPublica: committee data saved ({len(committee_map)} members)")

        return committee_map

    def enrich_trade_records(
        self,
        records: list,  # List[PoliticalTradeRecord]
        roster: Optional[Dict[str, dict]] = None,
        committee_map: Optional[Dict[str, List[str]]] = None,
    ) -> list:
        """
        Enrich a list of PoliticalTradeRecord objects with ProPublica data:
          - Real party affiliation
          - Committee names
          - Leadership role

        Safe to call with empty/None roster/committee_map.
        """
        if not roster:
            roster = {}
        if not committee_map:
            committee_map = {}

        enriched = []
        for record in records:
            try:
                last_name = record.politician_name.split()[-1].lower() if record.politician_name else ""
                state = record.state.lower() if record.state else ""
                key = f"{last_name}_{state}"

                member_info = roster.get(key, {})
                if member_info:
                    # Update party if unknown
                    if not record.party or record.party == "Unknown":
                        record.party = member_info.get("party_full", record.party)

                    # Update committee if missing
                    if not record.committee:
                        member_id = member_info.get("id", "")
                        committees = committee_map.get(member_id, [])
                        if committees:
                            record.committee = committees[0]  # Primary committee

                    # Update influence score based on leadership
                    leadership = member_info.get("leadership_role", "")
                    if leadership and not record.influence_score:
                        # Higher influence for leadership roles
                        record.influence_score = 85.0
            except Exception as e:
                logger.debug(f"Could not enrich record for {record.politician_name}: {e}")

            enriched.append(record)

        return enriched


# ─────────────────────────────────────────────
#  Convenience function
# ─────────────────────────────────────────────

def load_propublica_config() -> Optional[str]:
    """Load ProPublica API key from political_sources.yaml."""
    config_path = BASE_DIR / "configs" / "political_sources.yaml"
    if not config_path.exists():
        return None
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        sources = config.get("political_sources", {}).get("sources", {})
        key = sources.get("propublica", {}).get("api_key", "")
        return key if key else None
    except Exception:
        return None
