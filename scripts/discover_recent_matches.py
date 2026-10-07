#!/usr/bin/env python3
"""
scripts/discover_recent_matches.py
Discovers completed matches directly from ESPNcricinfo (Statsguru + Live results)
from a specified start date (default: 2026-09-18) to present.
Outputs:
- data/manifest_curated_delta.json (matches passing should_ingest_match for cricket-extractor)
- data/manifest_unfiltered_delta.json (all completed matches for cricket-lake-unfiltered)
"""
import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from curl_cffi import requests

# Set up path to import match_filter
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from src.match_filter import should_ingest_match

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("discover")

def parse_date(date_str: str) -> str:
    """Standardize date strings to YYYY-MM-DD."""
    for fmt in ('%b %d, %Y', '%Y-%m-%d', '%b %d-%d, %Y'):
        try:
            return datetime.strptime(date_str.split('-')[0].strip(), '%b %d, %Y').strftime('%Y-%m-%d')
        except Exception:
            pass
    return None

def discover_matches(since_date: str = "2026-09-18") -> dict:
    discovered = {}
    
    # 1. Statsguru 2026 across major classes
    classes = [
        (1, 'Test'), (2, 'ODI'), (3, 'T20I'), (6, 'T20'), (4, 'First-Class'), (5, 'List A')
    ]
    for cls, format_name in classes:
        url = f"https://stats.espncricinfo.com/ci/engine/records/team/match_results.html?class={cls};id=2026;type=year"
        try:
            resp = requests.get(url, impersonate="chrome124", timeout=20)
            if resp.status_code != 200:
                continue
            table_match = re.search(r'<table class="engineTable">(.*?)</table>', resp.text, re.DOTALL)
            if not table_match:
                continue
            trs = re.findall(r'<tr[^>]*>(.*?)</tr>', table_match.group(1), re.DOTALL)
            for tr in trs[1:]:
                m_id = re.search(r'/ci/engine/match/(\d+)\.html', tr)
                if not m_id:
                    continue
                mid = m_id.group(1)
                tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
                clean = [re.sub(r'<[^>]+>', '', x).strip() for x in tds]
                if len(clean) >= 6:
                    d = parse_date(clean[5])
                    if d and d >= since_date:
                        discovered[mid] = {
                            "match_id": mid,
                            "teams": [clean[0], clean[1]],
                            "format": format_name,
                            "season": "2026",
                            "date": d,
                            "winner": clean[2],
                            "result": clean[3],
                            "competition": None
                        }
        except Exception as e:
            logger.warning(f"Error scraping Statsguru class {cls}: {e}")

    # 2. Live results page for latest fixtures
    try:
        r = requests.get("https://www.espncricinfo.com/live-cricket-match-results", impersonate="chrome124", timeout=20)
        if r.status_code == 200:
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text)
            if m:
                data = json.loads(m.group(1))
                matches = data.get("props", {}).get("appPageProps", {}).get("data", {}).get("data", {}).get("content", {}).get("matches", [])
                for match in matches:
                    mid = str(match.get("objectId"))
                    start = (match.get("startDate") or "")[:10]
                    status = match.get("statusText", "")
                    teams = [t.get("team", {}).get("name") for t in match.get("teams", [])]
                    series = match.get("series", {}).get("longName") if match.get("series") else None
                    is_completed = any(w in status.lower() for w in ["won by", "tied", "drawn", "no result", "abandoned"])
                    if is_completed and start >= since_date:
                        fmt = match.get("format", "T20")
                        if mid not in discovered:
                            discovered[mid] = {
                                "match_id": mid,
                                "teams": teams,
                                "format": fmt,
                                "season": "2026",
                                "date": start,
                                "winner": None,
                                "result": status,
                                "competition": series
                            }
                        elif series:
                            discovered[mid]["competition"] = series
    except Exception as e:
        logger.warning(f"Error scraping live results: {e}")

    logger.info(f"Total discovered matches since {since_date}: {len(discovered)}")
    return discovered

def main():
    parser = argparse.ArgumentParser(description="Discover recent Cricinfo matches")
    parser.add_argument("--since", type=str, default="2026-09-18", help="Earliest match date (YYYY-MM-DD)")
    parser.add_argument("--output-curated", type=str, default="data/manifest_curated_delta.json")
    parser.add_argument("--output-unfiltered", type=str, default="data/manifest_unfiltered_delta.json")
    args = parser.parse_args()

    # Load existing manifests
    curated_manifest = REPO_ROOT / "data" / "manifest.json"
    existing_curated = set()
    if curated_manifest.exists():
        with open(curated_manifest) as f:
            for x in json.load(f):
                existing_curated.add(str(x["match_id"]))

    unfiltered_manifest = Path("/Users/hemanth/cricket-lake-unfiltered/data/manifest.json")
    existing_unfiltered = set(existing_curated)
    if unfiltered_manifest.exists():
        with open(unfiltered_manifest) as f:
            for x in json.load(f):
                existing_unfiltered.add(str(x["match_id"]))

    discovered = discover_matches(args.since)

    curated_delta = []
    unfiltered_delta = []

    for mid, info in discovered.items():
        if mid not in existing_unfiltered:
            unfiltered_delta.append(info)

        if mid not in existing_curated:
            meta = {
                "match_type": info["format"],
                "teams": info["teams"],
                "dates": [info["date"]],
                "event": {"name": info.get("competition")}
            }
            keep, _ = should_ingest_match(meta)
            if keep:
                curated_delta.append(info)

    # Sort chronologically
    curated_delta.sort(key=lambda x: (x.get("date") or "", x["match_id"]))
    unfiltered_delta.sort(key=lambda x: (x.get("date") or "", x["match_id"]))

    out_curated_path = REPO_ROOT / args.output_curated
    out_curated_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_curated_path, "w", encoding="utf-8") as f:
        json.dump(curated_delta, f, indent=2)

    out_unfiltered_path = REPO_ROOT / args.output_unfiltered
    out_unfiltered_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_unfiltered_path, "w", encoding="utf-8") as f:
        json.dump(unfiltered_delta, f, indent=2)

    logger.info(f"Saved {len(curated_delta)} matches to {out_curated_path}")
    logger.info(f"Saved {len(unfiltered_delta)} matches to {out_unfiltered_path}")

if __name__ == "__main__":
    main()
