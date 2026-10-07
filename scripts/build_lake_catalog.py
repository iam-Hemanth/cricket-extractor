#!/usr/bin/env python3
"""
scripts/build_lake_catalog.py
Generates matches_index.csv, README.txt, cricket_lake_v3.zip, and updated cricket_lake_v3.tar.gz.
Provides an exhaustive catalog pointing every JSON file to its match details.
"""
import csv
import json
import os
import sys
import tarfile
import zipfile
from pathlib import Path


def generate_catalog(matches_dir: Path, output_dir: Path, archive_prefix: str = "cricket_lake_v3"):
    output_dir.mkdir(parents=True, exist_ok=True)
    json_files = sorted(
        [p for p in matches_dir.glob("*.json") if p.name not in ("lake_manifest.json", "manifest.json", "failed_matches.json")],
        key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem
    )
    print(f"Found {len(json_files)} match JSON files to index.")

    rows = []
    for idx, p in enumerate(json_files, 1):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print(f"Error loading {p.name}: {e}")
            continue

        mid = str(d.get("match_id") or p.stem)
        meta = d.get("meta") or {}
        dates = meta.get("dates") or []
        m_date = dates[0] if dates else "Unknown"
        fmt = meta.get("format") or "Unknown"
        bucket = meta.get("format_bucket") or fmt
        comp = meta.get("competition") or ""
        season = str(meta.get("season") or "")
        venue = meta.get("venue") or ""
        city = meta.get("city") or ""
        v_country = meta.get("venue_country") or ""

        # Extract teams
        inns = d.get("innings") or []
        if inns and len(inns) > 0:
            team1 = inns[0].get("batting_team") or ""
            team2 = inns[0].get("bowling_team") or ""
        else:
            xi_teams = [t for t in (d.get("playing_xi") or {}).keys() if not t.startswith("_")]
            team1 = xi_teams[0] if len(xi_teams) > 0 else ""
            team2 = xi_teams[1] if len(xi_teams) > 1 else ""

        # Outcome
        outcome = d.get("outcome") or {}
        winner = outcome.get("winner") or ""
        by_runs = outcome.get("win_by_runs")
        by_wkts = outcome.get("win_by_wickets")
        if by_runs:
            result_str = f"{winner} won by {by_runs} runs"
        elif by_wkts:
            result_str = f"{winner} won by {by_wkts} wickets"
        elif outcome.get("result"):
            result_str = outcome.get("result")
        elif winner:
            result_str = f"{winner} won"
        else:
            result_str = "No result"

        # Toss
        toss = d.get("toss") or {}
        toss_winner = toss.get("winner") or ""
        toss_decision = toss.get("decision") or ""

        # Deliveries metrics
        total_balls = 0
        total_runs = 0
        total_wkts = 0
        wagon_balls = 0
        pitch_balls = 0
        shot_balls = 0

        for inn in inns:
            for ov in (inn.get("overs") or []):
                for b in (ov.get("deliveries") or []):
                    total_balls += 1
                    total_runs += (b.get("runs") or {}).get("total", 0)
                    total_wkts += len(b.get("wickets") or [])
                    tact = b.get("tactical") or {}
                    if tact.get("wagon_coords") is not None:
                        wagon_balls += 1
                    if tact.get("pitch_line") is not None:
                        pitch_balls += 1
                    if tact.get("shot_type") is not None:
                        shot_balls += 1

        rows.append({
            "filename": f"{mid}.json",
            "match_id": mid,
            "date": m_date,
            "format": fmt,
            "format_bucket": bucket,
            "competition": comp,
            "season": season,
            "team1": team1,
            "team2": team2,
            "winner": winner,
            "result": result_str,
            "toss_winner": toss_winner,
            "toss_decision": toss_decision,
            "venue": venue,
            "city": city,
            "venue_country": v_country,
            "total_balls": total_balls,
            "total_runs": total_runs,
            "total_wickets": total_wkts,
            "wagon_wheel_balls": wagon_balls,
            "pitch_map_balls": pitch_balls,
            "shot_type_balls": shot_balls
        })

    # Sort rows chronologically
    rows.sort(key=lambda r: (r["date"], r["match_id"]))

    # 1. Write matches_index.csv
    csv_path = output_dir / "matches_index.csv"
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Generated CSV catalog at: {csv_path} ({len(rows)} matches)")

    # 2. Write README.txt
    txt_path = output_dir / "README.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("=" * 120 + "\n")
        f.write("🏏 ESPNcricinfo Master Cricket Data Lake v3.0 — Match Catalog & Dataset Guide\n")
        f.write("=" * 120 + "\n\n")
        f.write(f"Total Matches     : {len(rows):,} matches\n")
        total_balls_all = sum(r["total_balls"] for r in rows)
        total_runs_all = sum(r["total_runs"] for r in rows)
        total_wkts_all = sum(r["total_wickets"] for r in rows)
        wagon_balls_all = sum(r["wagon_wheel_balls"] for r in rows)
        pitch_balls_all = sum(r["pitch_map_balls"] for r in rows)
        shot_balls_all = sum(r["shot_type_balls"] for r in rows)
        f.write(f"Total Deliveries  : {total_balls_all:,} deliveries\n")
        f.write(f"Total Runs        : {total_runs_all:,} runs\n")
        f.write(f"Total Wickets     : {total_wkts_all:,} wickets\n")
        f.write(f"Date Range        : {rows[0]['date']} to {rows[-1]['date']}\n")
        f.write(f"Tactical Telemetry: Wagon Wheel ({wagon_balls_all:,} balls, {wagon_balls_all/total_balls_all*100:.1f}%), ")
        f.write(f"Pitch Map ({pitch_balls_all:,} balls, {pitch_balls_all/total_balls_all*100:.1f}%), ")
        f.write(f"Shot Types ({shot_balls_all:,} balls, {shot_balls_all/total_balls_all*100:.1f}%)\n\n")

        f.write("DATASET STRUCTURE & HOW TO USE:\n")
        f.write("-" * 120 + "\n")
        f.write("1. Each match is stored as an independent JSON file named '<match_id>.json'.\n")
        f.write("2. 'matches_index.csv' contains a tabular index of all matches with dates, teams, venues, and scores.\n")
        f.write("   - Load directly with pandas: pd.read_csv('matches_index.csv')\n")
        f.write("3. Key JSON fields in every file:\n")
        f.write("   - meta       : Competition, format, season, dates, venue, balls_per_over, tactical_coverage\n")
        f.write("   - outcome    : Winner, margin (win_by_runs / win_by_wickets), eliminator\n")
        f.write("   - toss       : Toss winner and decision ('bat' or 'field')\n")
        f.write("   - playing_xi : Team -> list of 11 players with batting/bowling style and canonical Cricsheet IDs\n")
        f.write("   - innings    : Ball-by-ball delivery log with wagon wheel coordinates, Hawk-Eye pitch line/length\n\n")

        f.write("MATCH CATALOG (5,483 MATCHES CHRONOLOGICAL):\n")
        f.write("-" * 120 + "\n")
        f.write(f"{'Filename':<14} {'Date':<12} {'Format':<8} {'Teams':<36} {'Competition / Series':<32} {'Result'}\n")
        f.write("-" * 120 + "\n")

        for r in rows:
            teams_str = f"{r['team1'][:16]} vs {r['team2'][:16]}"
            comp_str = (r['competition'] or r['format_bucket'])[:30]
            res_str = r['result'][:28]
            f.write(f"{r['filename']:<14} {r['date']:<12} {r['format_bucket']:<8} {teams_str:<36} {comp_str:<32} {res_str}\n")

        f.write("=" * 120 + "\n")

    print(f"Generated README.txt catalog at: {txt_path}")

    # 3. Create zip archive
    zip_path = output_dir / f"{archive_prefix}.zip"
    print(f"Packaging {zip_path} (this may take ~15-20s)...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(txt_path, arcname="README.txt")
        zf.write(csv_path, arcname="matches_index.csv")
        manifest_p = matches_dir / "lake_manifest.json"
        if manifest_p.exists():
            zf.write(manifest_p, arcname="lake_manifest.json")
        for p in json_files:
            zf.write(p, arcname=p.name)
    print(f"Created {zip_path} ({zip_path.stat().st_size / (1024*1024):.2f} MB)")

    # 4. Create tar.gz archive
    tar_path = output_dir / f"{archive_prefix}.tar.gz"
    print(f"Packaging {tar_path}...")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(txt_path, arcname="README.txt")
        tf.add(csv_path, arcname="matches_index.csv")
        manifest_p = matches_dir / "lake_manifest.json"
        if manifest_p.exists():
            tf.add(manifest_p, arcname="lake_manifest.json")
        for p in json_files:
            tf.add(p, arcname=p.name)
    print(f"Created {tar_path} ({tar_path.stat().st_size / (1024*1024):.2f} MB)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build match catalog and distribution archives")
    parser.add_argument("--matches-dir", type=str, default="data/master_lake_matches", help="Directory of JSON matches")
    parser.add_argument("--output-dir", type=str, default="data/master_lake_dist", help="Directory to output catalog & archives")
    parser.add_argument("--archive-prefix", type=str, default="cricket_lake_v3", help="Prefix for .zip and .tar.gz archives")
    args = parser.parse_args()

    matches_dir = Path(args.matches_dir)
    output_dir = Path(args.output_dir)
    generate_catalog(matches_dir, output_dir, archive_prefix=args.archive_prefix)


if __name__ == "__main__":
    main()
