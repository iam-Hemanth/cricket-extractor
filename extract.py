#!/usr/bin/env python3
"""
extract.py
Production CLI Extractor for ESPNcricinfo Data Lake.
Fetches, stores raw byte-for-byte responses, constructs Match JSON v3.0,
validates integrity, and outputs to the data lake.
"""
import argparse
import csv
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.client import CircuitBreakerTripped, CricinfoClient
from src.commentary import fetch_innings_commentary, fetch_match_details
from src.constructor import construct_match_v3
from src.cricsheet_audit import CricsheetAuditor
from src.validator import validate_innings, validate_player_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("extractor")


def load_references(repo_root: Path) -> Tuple[dict, dict, dict]:
    """Load player profiles, people registry, and venue mappings."""
    profiles_path = repo_root / "data" / "player_profiles_cache.json"
    player_profiles = {}
    if profiles_path.exists():
        with open(profiles_path, "r", encoding="utf-8") as f:
            player_profiles = json.load(f)

    venue_path = repo_root / "data" / "venue_to_country.json"
    venue_map = {}
    if venue_path.exists():
        with open(venue_path, "r", encoding="utf-8") as f:
            venue_map = json.load(f)

    people_path = repo_root / "data" / "people.csv"
    people_registry = {}
    if people_path.exists():
        with open(people_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = row.get("identifier")
                cricinfo_id = row.get("key_cricinfo")
                if cricinfo_id and cid:
                    people_registry[str(cricinfo_id)] = cid

    return player_profiles, people_registry, venue_map


def get_target_match_ids(args: argparse.Namespace, repo_root: Path) -> List[str]:
    """Resolve target match IDs from args (CLI, file, or manifest batch)."""
    if args.match_ids:
        return [m.strip() for m in args.match_ids.split(",") if m.strip()]

    if args.ids_file:
        p = Path(args.ids_file)
        if not p.exists():
            logger.error(f"IDs file not found: {p}")
            sys.exit(1)
        if p.suffix == ".json":
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [str(m).strip() for m in data if str(m).strip()]
                elif isinstance(data, dict):
                    return [str(m).strip() for m in data.keys()]
        else:
            with open(p, "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    manifest_path = Path(args.manifest) if args.manifest else (repo_root / "data" / "manifest.json")
    if not manifest_path.exists():
        logger.error(f"Manifest not found: {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    all_ids = [str(item["match_id"]) if isinstance(item, dict) else str(item) for item in manifest]
    start_idx = args.batch_index * args.batch_size
    end_idx = start_idx + args.batch_size
    batch_ids = all_ids[start_idx:end_idx]
    logger.info(f"Resolved batch {args.batch_index}: slice [{start_idx}:{end_idx}] -> {len(batch_ids)} matches")
    return batch_ids


def atomic_write_json(file_path: Path, data: Any):
    """Write data to a temp file and atomically rename to avoid partial writes."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(".tmp.json")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(file_path)


def main():
    parser = argparse.ArgumentParser(description="ESPNcricinfo Lake Match Extractor")
    parser.add_argument("--batch-index", type=int, default=0, help="Batch index for manifest slice")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for manifest slice")
    parser.add_argument("--match-ids", type=str, default=None, help="Comma-separated list of match IDs")
    parser.add_argument("--ids-file", type=str, default=None, help="Path to text or JSON file of match IDs")
    parser.add_argument("--manifest", type=str, default=None, help="Path to manifest.json")
    parser.add_argument("--raw-dir", type=str, default="data/raw", help="Path to store raw HTTP payloads")
    parser.add_argument("--output-dir", type=str, default="data/matches", help="Path to store parsed Match v3 JSONs")
    parser.add_argument("--cricsheet-dir", type=str, default=None, help="Path to local Cricsheet JSON dir for audit")
    parser.add_argument("--delay", type=float, default=0.6, help="Base delay between requests in seconds")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already-extracted matches")
    parser.add_argument("--circuit-breaker-limit", type=int, default=5, help="Circuit breaker trip threshold")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    player_profiles, people_registry, venue_map = load_references(repo_root)
    auditor = None
    if args.cricsheet_dir and Path(args.cricsheet_dir).exists():
        auditor = CricsheetAuditor(str(repo_root / "data" / "people.csv"))

    target_ids = get_target_match_ids(args, repo_root)
    total_targets = len(target_ids)
    logger.info(f"Starting extraction of {total_targets} matches...")

    client = CricinfoClient(breaker_threshold=args.circuit_breaker_limit)

    succeeded = 0
    failed_matches: Dict[str, str] = {}
    skipped = 0

    failed_log_path = output_dir / "failed_matches.json"

    try:
        for idx, mid in enumerate(target_ids, 1):
            out_file = output_dir / f"{mid}.json"
            if args.skip_existing and out_file.exists():
                logger.info(f"[{idx}/{total_targets}] Match {mid} exists, skipping (--skip-existing)")
                skipped += 1
                continue

            logger.info(f"[{idx}/{total_targets}] Extracting match {mid}...")
            match_raw_dir = raw_dir / str(mid)

            # 1. Fetch details & scorecard
            details, err = fetch_match_details(
                client=client,
                match_id=mid,
                raw_dir=raw_dir,
                delay_sec=args.delay
            )
            if err or not details:
                logger.warning(f"Failed to fetch details for match {mid}: {err}")
                failed_matches[mid] = f"Details fetch failed: {err}"
                continue

            match_info = details.get("match") or {}
            content = details.get("content") or {}
            series_id = str(
                (match_info.get("series") or {}).get("objectId")
                or (match_info.get("series") or {}).get("id")
                or ""
            )

            # 2. Fetch innings commentary
            innings_list = content.get("innings") or []
            commentary_chunks_by_inn: Dict[int, List[dict]] = {}
            inn_fetch_failed = False

            for inn_idx, inn in enumerate(innings_list, 1):
                inn_num = inn.get("inningNumber") or inn_idx
                chunks, c_err = fetch_innings_commentary(
                    client=client,
                    series_id=series_id,
                    match_id=mid,
                    inn_num=inn_num,
                    raw_dir=raw_dir,
                    delay_sec=args.delay
                )

                if c_err:
                    logger.warning(f"Failed commentary for match {mid} inn {inn_num}: {c_err}")
                    failed_matches[mid] = f"Commentary inn {inn_num} failed: {c_err}"
                    inn_fetch_failed = True
                    break
                commentary_chunks_by_inn[inn_idx] = chunks

            if inn_fetch_failed:
                continue

            # 3. Construct Match v3.0
            match_v3, const_err = construct_match_v3(
                mid=mid,
                raw_details=details,
                commentary_chunks_by_inn=commentary_chunks_by_inn,
                player_profiles=player_profiles,
                people_registry=people_registry,
                venue_map=venue_map
            )
            if const_err or not match_v3:
                logger.warning(f"Failed to construct match {mid}: {const_err}")
                failed_matches[mid] = f"Construction error: {const_err}"
                continue

            # 4. Multi-dimension validation
            validation_errors = []
            sc_inns = content.get("innings") or []
            for i_idx, v_inn in enumerate(match_v3.get("innings", [])):
                sc_inn_data = sc_inns[i_idx] if i_idx < len(sc_inns) else None
                is_last = (i_idx == len(match_v3.get("innings", [])) - 1)
                is_ok, errs = validate_innings(v_inn, sc_inn_data, is_last_innings=is_last)
                if not is_ok:
                    validation_errors.extend(errs)

            _, reg_errs = validate_player_registry(match_v3)
            validation_errors.extend(reg_errs)

            if validation_errors:
                logger.warning(f"Validation failed for match {mid} ({len(validation_errors)} errors): {validation_errors[:2]}")
                failed_matches[mid] = f"Validation errors: {'; '.join(validation_errors[:3])}"
                continue

            # 5. Optional Cricsheet Audit
            if auditor and args.cricsheet_dir:
                cs_file = Path(args.cricsheet_dir) / f"{mid}.json"
                if cs_file.exists():
                    try:
                        with open(cs_file, "r", encoding="utf-8") as f:
                            cs_data = json.load(f)
                        audit_res = auditor.audit_match(match_v3, cs_data)
                        if not audit_res.is_valid:
                            logger.warning(f"Cricsheet audit failed for {mid}: {audit_res.hard_errors[:2]}")
                            # Soft warn vs hard fail: record but write
                            match_v3["meta"]["audit_status"] = "audit_warning"
                        else:
                            match_v3["meta"]["audit_status"] = "audit_verified"
                    except Exception as e:
                        logger.warning(f"Could not run Cricsheet audit for {mid}: {e}")

            # 6. Atomic write
            atomic_write_json(out_file, match_v3)
            succeeded += 1
            logger.info(f"Successfully extracted & validated match {mid} -> {out_file.name}")

            # Jittered polite backoff
            time.sleep(args.delay + random.uniform(0.1, 0.4))

    except CircuitBreakerTripped as cbe:
        logger.critical(f"CIRCUIT BREAKER TRIPPED: {cbe}")
        if failed_matches:
            with open(failed_log_path, "w", encoding="utf-8") as f:
                json.dump(failed_matches, f, indent=2)
        sys.exit(2)

    except KeyboardInterrupt:
        logger.info("Extraction interrupted by user.")

    finally:
        # Save failed matches log
        if failed_matches:
            with open(failed_log_path, "w", encoding="utf-8") as f:
                json.dump(failed_matches, f, indent=2)
            logger.info(f"Wrote {len(failed_matches)} failed matches to {failed_log_path}")

        logger.info("=" * 60)
        logger.info(f"Extraction Batch Complete.")
        logger.info(f"Total: {total_targets} | Succeeded: {succeeded} | Skipped: {skipped} | Failed: {len(failed_matches)}")
        logger.info("=" * 60)

    sys.exit(0)


if __name__ == "__main__":
    main()
