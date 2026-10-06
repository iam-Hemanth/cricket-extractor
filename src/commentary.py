"""
src/commentary.py
Commentary and Scorecard Retriever with Byte-for-Byte Raw Storage.
"""
import gzip
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.auth import generate_url_token
from src.client import CricinfoClient

BASE_URL = "https://hs-consumer-api.cricinfo.com"

def save_raw_payload(
    raw_match_dir: Path,
    filename: str,
    content_bytes: bytes,
    url: str,
    status_code: int
):
    """Save an unfiltered HTTP response body gzipped alongside metadata."""
    raw_match_dir.mkdir(parents=True, exist_ok=True)
    gz_path = raw_match_dir / f"{filename}.gz"
    with gzip.open(gz_path, "wb") as f:
        f.write(content_bytes)

    meta_path = raw_match_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    sha256 = hashlib.sha256(content_bytes).hexdigest()
    meta[filename] = {
        "url": url,
        "status_code": status_code,
        "sha256": sha256,
        "bytes_length": len(content_bytes),
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

def fetch_match_details(
    client: CricinfoClient,
    match_id: str,
    series_id: Optional[str] = None,
    raw_dir: Optional[Path] = None,
    delay_sec: float = 0.5
) -> Tuple[Optional[dict], Optional[str]]:
    """Fetch complete match details and scorecard."""
    import re

    # If series_id is known, attempt consumer API
    if series_id:
        path = f"/v1/pages/match/details?lang=en&seriesId={series_id}&matchId={match_id}&latest=true"
        token = generate_url_token(path)
        url = f"{BASE_URL}{path}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Origin": "https://www.espncricinfo.com",
            "Referer": "https://www.espncricinfo.com/",
            "Accept": "application/json",
            "x-hsci-auth-token": token
        }
        r, err = client.get(url, headers=headers, delay_sec=delay_sec)
        if r and r.status_code == 200:
            content_bytes = r.content
            if raw_dir:
                save_raw_payload(raw_dir / str(match_id), "scorecard.json", content_bytes, url, r.status_code)
            try:
                data = json.loads(content_bytes.decode("utf-8"))
                return data, None
            except Exception as e:
                return None, f"JSON parse error: {e}"

    # Primary / Fallback: Fetch via stats engine HTML (__NEXT_DATA__)
    engine_url = f"https://stats.espncricinfo.com/ci/engine/match/{match_id}.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    r, err = client.get(engine_url, headers=headers, delay_sec=delay_sec)
    if err or not r:
        return None, err or f"Failed fetching base match {match_id}"

    if '<script id="__NEXT_DATA__"' not in r.text:
        return None, f"No __NEXT_DATA__ script found in {engine_url}"

    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text)
    if not m:
        return None, "Failed extracting __NEXT_DATA__ regex"

    try:
        full_data = json.loads(m.group(1))
        app_props = full_data.get("props", {}).get("appPageProps", {}).get("data", {})
        if not app_props:
            return None, "Empty appPageProps.data in Next.js payload"

        content_bytes = json.dumps(app_props, ensure_ascii=False).encode("utf-8")
        if raw_dir:
            save_raw_payload(raw_dir / str(match_id), "scorecard.json", content_bytes, engine_url, r.status_code)

        return app_props, None
    except Exception as e:
        return None, f"JSON parse error from __NEXT_DATA__: {e}"


def fetch_innings_commentary(
    client: CricinfoClient,
    series_id: str,
    match_id: str,
    inn_num: int,
    raw_dir: Optional[Path] = None,
    delay_sec: float = 0.5
) -> Tuple[List[dict], Optional[str]]:
    """
    Fetch complete over-by-over commentary for an innings.
    Saves unfiltered raw chunks to raw_dir if specified.
    """
    all_chunks = []
    seen_overs = set()
    next_over = None
    chunk_idx = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin": "https://www.espncricinfo.com",
        "Referer": "https://www.espncricinfo.com/",
        "Accept": "application/json"
    }

    while True:
        qs = f"?seriesId={series_id}&matchId={match_id}&inningNumber={inn_num}&commentType=ALL"
        if next_over is not None:
            qs += f"&fromInningOver={next_over}"
        path = f"/v1/pages/match/comments{qs}"
        headers["x-hsci-auth-token"] = generate_url_token(path)
        url = f"{BASE_URL}{path}"

        r, err = client.get(url, headers=headers, delay_sec=delay_sec)
        if err or not r:
            return all_chunks, err

        content_bytes = r.content
        if raw_dir:
            save_raw_payload(
                raw_dir / str(match_id),
                f"commentary_inn{inn_num}_chunk{chunk_idx}.json",
                content_bytes,
                url,
                r.status_code
            )
        chunk_idx += 1

        try:
            data = json.loads(content_bytes.decode("utf-8"))
        except Exception as e:
            return all_chunks, f"JSON parse error in commentary chunk: {e}"

        comments = data.get("comments") or []
        if not comments:
            break

        all_chunks.extend(comments)

        nxt = data.get("nextInningOver")
        if not nxt or nxt in seen_overs:
            break
        seen_overs.add(nxt)
        next_over = nxt

    return all_chunks, None


def load_raw_match_payloads(raw_match_dir: Path) -> Tuple[Optional[dict], Dict[int, List[dict]], Optional[str]]:
    """
    Load raw match payloads from raw_match_dir (gzipped json files).
    Returns: (raw_details, commentary_chunks_by_inn, error_str)
    """
    import re
    sc_path = raw_match_dir / "scorecard.json.gz"
    if not sc_path.exists():
        return None, {}, f"Scorecard file not found: {sc_path}"

    try:
        with gzip.open(sc_path, "rt", encoding="utf-8") as f:
            raw_details = json.load(f)
    except Exception as e:
        return None, {}, f"Failed to read scorecard.json.gz: {e}"

    commentary_by_inn: Dict[int, List[dict]] = {}
    chunk_files = sorted(raw_match_dir.glob("commentary_inn*_chunk*.json.gz"))
    for cf in chunk_files:
        m = re.search(r"commentary_inn(\d+)_chunk", cf.name)
        if not m:
            continue
        inn_num = int(m.group(1))
        try:
            with gzip.open(cf, "rt", encoding="utf-8") as f:
                chunk_data = json.load(f)
                comments = chunk_data.get("comments") or []
                commentary_by_inn.setdefault(inn_num, []).extend(comments)
        except Exception as e:
            return raw_details, commentary_by_inn, f"Failed to read chunk {cf.name}: {e}"

    return raw_details, commentary_by_inn, None

