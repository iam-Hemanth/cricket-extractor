#!/usr/bin/env python3
"""
consolidate.py
Data Lake Consolidation & Manifest Generator.
Gathers all parsed Match JSON v3.0 files, validates structural integrity,
computes global data lake metrics (formats, tactical coverage, date ranges, players),
writes a comprehensive `lake_manifest.json`, and packages `cricket_lake_v3.tar.gz`.
"""
import argparse
import glob
import json
import logging
import os
import sys
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("consolidator")


def scan_and_consolidate(
    input_dirs: List[Path],
    output_dir: Path,
    manifest_file: Path,
    archive_file: Optional[Path] = None
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    match_files: Dict[str, Path] = {}
    failed_matches_all: Dict[str, str] = {}

    for d in input_dirs:
        if not d.exists():
            continue
        # Check for failed_matches.json
        fail_file = d / "failed_matches.json"
        if fail_file.exists():
            try:
                with open(fail_file, "r", encoding="utf-8") as f:
                    failed_matches_all.update(json.load(f))
            except Exception as e:
                logger.warning(f"Failed to read {fail_file}: {e}")

        # Scan for match json files
        for p in d.rglob("*.json"):
            if p.name in ("failed_matches.json", "manifest.json", "lake_manifest.json", "meta.json"):
                continue
            mid = p.stem
            if mid.isdigit():
                match_files[mid] = p

    logger.info(f"Discovered {len(match_files)} unique parsed matches across input directories.")

    # Aggregate metrics
    format_counts: Counter = Counter()
    bucket_counts: Counter = Counter()
    season_counts: Counter = Counter()
    total_balls = 0
    total_runs = 0
    total_wickets = 0
    wagon_balls = 0
    pitch_balls = 0
    shot_balls = 0
    min_date = "9999-99-99"
    max_date = "0000-00-00"
    unique_players: Set[str] = set()
    mapped_players: Set[str] = set()

    processed_mids: List[str] = []

    for idx, (mid, p) in enumerate(sorted(match_files.items()), 1):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Corrupt JSON for match {mid} at {p}: {e}")
            failed_matches_all[mid] = f"Corrupt JSON: {e}"
            continue

        meta = data.get("meta") or {}
        fmt = meta.get("format") or "Unknown"
        bucket = meta.get("format_bucket") or fmt
        season = str(meta.get("season") or "Unknown")

        format_counts[fmt] += 1
        bucket_counts[bucket] += 1
        season_counts[season] += 1

        dates = meta.get("dates") or []
        for dt in dates:
            if dt < min_date:
                min_date = dt
            if dt > max_date:
                max_date = dt

        # Player counts
        xi = data.get("playing_xi") or {}
        for team, players in xi.items():
            if not isinstance(players, list):
                continue
            for pl in players:
                if isinstance(pl, dict):
                    pid = pl.get("player_id")
                    if pid:
                        unique_players.add(pid)
                        if pl.get("cricsheet_id"):
                            mapped_players.add(pid)
                elif isinstance(pl, str):
                    unique_players.add(pl)

        # Innings metrics
        for inn in data.get("innings") or []:
            for ov in inn.get("overs") or []:
                for d in ov.get("deliveries") or []:
                    total_balls += 1
                    total_runs += d.get("runs", {}).get("total", 0)
                    total_wickets += len(d.get("wickets") or [])
                    tact = d.get("tactical") or {}
                    if tact.get("wagon_coords") is not None:
                        wagon_balls += 1
                    if tact.get("pitch_line") is not None:
                        pitch_balls += 1
                    if tact.get("shot_type") is not None:
                        shot_balls += 1

        # Copy/Symlink or ensure present in output_dir
        dest = output_dir / f"{mid}.json"
        if dest.resolve() != p.resolve():
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        processed_mids.append(mid)

    # Any match that was previously marked failed but now successfully processed should be removed from failures
    for mid in processed_mids:
        failed_matches_all.pop(mid, None)

    total_matches = len(processed_mids)
    wagon_pct = round((wagon_balls / total_balls * 100), 2) if total_balls > 0 else 0.0
    pitch_pct = round((pitch_balls / total_balls * 100), 2) if total_balls > 0 else 0.0
    shot_pct = round((shot_balls / total_balls * 100), 2) if total_balls > 0 else 0.0

    manifest = {
        "dataset_name": "cricket-lake-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "3.0.0",
        "total_matches": total_matches,
        "date_range": {
            "earliest": min_date if min_date != "9999-99-99" else None,
            "latest": max_date if max_date != "0000-00-00" else None
        },
        "totals": {
            "deliveries": total_balls,
            "runs": total_runs,
            "wickets": total_wickets,
            "deliveries_with_wagon_wheel": wagon_balls,
            "deliveries_with_pitch_coordinates": pitch_balls,
            "deliveries_with_shot_type": shot_balls,
            "wagon_wheel_coverage_pct": wagon_pct,
            "pitch_coordinates_coverage_pct": pitch_pct,
            "shot_type_coverage_pct": shot_pct
        },
        "players": {
            "unique_players_count": len(unique_players),
            "players_with_cricsheet_mapping": len(mapped_players),
            "mapping_rate_pct": round(len(mapped_players) / len(unique_players) * 100, 2) if unique_players else 0.0
        },
        "formats_breakdown": dict(format_counts.most_common()),
        "format_buckets_breakdown": dict(bucket_counts.most_common()),
        "top_seasons": dict(season_counts.most_common(15)),
        "unreconciled_failures": {
            "count": len(failed_matches_all),
            "matches": failed_matches_all
        }
    }

    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written to {manifest_file} (Total: {total_matches} valid matches)")

    if archive_file:
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Creating consolidated archive {archive_file}...")
        with tarfile.open(archive_file, "w:gz") as tar:
            for mid in sorted(processed_mids):
                m_path = output_dir / f"{mid}.json"
                tar.add(m_path, arcname=f"{mid}.json")
            tar.add(manifest_file, arcname="lake_manifest.json")
        logger.info(f"Consolidated archive created: {archive_file} ({archive_file.stat().st_size / (1024*1024):.2f} MB)")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Consolidate parsed matches and create lake manifest")
    parser.add_argument("--input-dirs", nargs="+", required=True, help="Input directories containing match JSON files")
    parser.add_argument("--output-dir", type=str, default="data/master_matches", help="Directory for consolidated match JSONs")
    parser.add_argument("--manifest-path", type=str, default="data/lake_manifest.json", help="Path to write lake_manifest.json")
    parser.add_argument("--archive-path", type=str, default=None, help="Optional path to write consolidated .tar.gz archive")
    args = parser.parse_args()

    input_paths = [Path(p) for p in args.input_dirs]
    out_dir = Path(args.output_dir)
    manifest_p = Path(args.manifest_path)
    archive_p = Path(args.archive_path) if args.archive_path else None

    scan_and_consolidate(
        input_dirs=input_paths,
        output_dir=out_dir,
        manifest_file=manifest_p,
        archive_file=archive_p
    )


if __name__ == "__main__":
    main()
