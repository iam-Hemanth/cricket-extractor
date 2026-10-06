#!/usr/bin/env python3
"""
reparse.py
Offline Re-parser CLI for ESPNcricinfo Data Lake.
Reads raw gzipped HTTP payloads from disk, parses Match JSON v3.0,
validates integrity, and optionally tests parsing determinism.
ZERO network calls - operates 100% offline.
"""
import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.commentary import load_raw_match_payloads
from src.constructor import construct_match_v3
from src.validator import validate_innings, validate_player_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("reparser")


def load_references(repo_root: Path) -> Tuple[dict, dict, dict]:
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


def main():
    parser = argparse.ArgumentParser(description="Offline Match JSON v3.0 Re-Parser")
    parser.add_argument("--raw-dir", type=str, default="data/raw", help="Path to raw HTTP payloads directory")
    parser.add_argument("--output-dir", type=str, default="data/matches", help="Path to output parsed matches")
    parser.add_argument("--match-ids", type=str, default=None, help="Comma-separated match IDs to reparse")
    parser.add_argument("--verify-determinism", action="store_true", help="Assert offline parser determinism")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        logger.error(f"Raw directory not found: {raw_dir}")
        sys.exit(1)

    player_profiles, people_registry, venue_map = load_references(repo_root)

    # Determine match directories to reparse
    if args.match_ids:
        target_mids = [m.strip() for m in args.match_ids.split(",") if m.strip()]
        match_dirs = [raw_dir / mid for mid in target_mids if (raw_dir / mid).is_dir()]
    else:
        match_dirs = sorted([p for p in raw_dir.iterdir() if p.is_dir() and (p / "scorecard.json.gz").exists()])

    logger.info(f"Found {len(match_dirs)} matches to reparse in {raw_dir}...")

    succeeded = 0
    failed = 0
    determinism_failures = 0

    for idx, mdir in enumerate(match_dirs, 1):
        mid = mdir.name
        logger.info(f"[{idx}/{len(match_dirs)}] Reparsing match {mid}...")

        details, chunks_by_inn, err = load_raw_match_payloads(mdir)
        if err or not details:
            logger.warning(f"Failed to load raw payloads for match {mid}: {err}")
            failed += 1
            continue

        match_v3, const_err = construct_match_v3(
            mid=mid,
            raw_details=details,
            commentary_chunks_by_inn=chunks_by_inn,
            player_profiles=player_profiles,
            people_registry=people_registry,
            venue_map=venue_map
        )
        if const_err or not match_v3:
            logger.warning(f"Failed to construct match {mid}: {const_err}")
            failed += 1
            continue

        # Determinism check
        if args.verify_determinism:
            match_v3_second, _ = construct_match_v3(
                mid=mid,
                raw_details=details,
                commentary_chunks_by_inn=chunks_by_inn,
                player_profiles=player_profiles,
                people_registry=people_registry,
                venue_map=venue_map
            )
            dump1 = json.dumps(match_v3, sort_keys=True)
            dump2 = json.dumps(match_v3_second, sort_keys=True)
            if dump1 != dump2:
                logger.error(f"DETERMINISM FAILED on match {mid}: Two parse passes yielded different output!")
                determinism_failures += 1
            else:
                logger.info(f"Determinism verified for match {mid} ✓")

        # Multi-dimension validation
        validation_errors = []
        sc_inns = (details.get("content") or {}).get("innings") or []
        for i_idx, v_inn in enumerate(match_v3.get("innings", [])):
            sc_inn_data = sc_inns[i_idx] if i_idx < len(sc_inns) else None
            is_last = (i_idx == len(match_v3.get("innings", [])) - 1)
            is_ok, errs = validate_innings(v_inn, sc_inn_data, is_last_innings=is_last)
            if not is_ok:
                validation_errors.extend(errs)

        if validation_errors:
            logger.warning(f"Validation failed for match {mid}: {validation_errors[:2]}")
            failed += 1
            continue

        # Write output file
        out_file = output_dir / f"{mid}.json"
        temp_file = output_dir / f"{mid}.json.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(match_v3, f, indent=2, ensure_ascii=False)
        temp_file.replace(out_file)
        succeeded += 1

    logger.info("=" * 60)
    logger.info(f"Offline Reparse Complete: {succeeded} succeeded | {failed} failed")
    if args.verify_determinism:
        logger.info(f"Determinism Checks: {len(match_dirs) - determinism_failures}/{len(match_dirs)} passed")
    logger.info("=" * 60)

    if determinism_failures > 0 or failed > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
