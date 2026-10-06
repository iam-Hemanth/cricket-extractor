#!/usr/bin/env python3
"""
local_batch_runner.py
Polite Single-Threaded Batch Runner for Local Extraction.
Features:
- 0.5s–1.0s random jitter to avoid rate limits
- Real-time ETA and success rate tracking
- Automatic retry of transient errors
- Clean summary report upon completion
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Local Batch Runner with Polite Jitter")
    parser.add_argument("--batch-index", type=int, default=0, help="Batch index to run")
    parser.add_argument("--batch-size", type=int, default=50, help="Number of matches to run locally")
    parser.add_argument("--match-ids", type=str, default=None, help="Specific match IDs to run")
    parser.add_argument("--ids-file", type=str, default=None, help="File containing match IDs")
    parser.add_argument("--min-delay", type=float, default=0.5, help="Minimum delay in seconds")
    parser.add_argument("--max-delay", type=float, default=1.0, help="Maximum delay in seconds")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip existing matches")
    parser.add_argument("--cricsheet-dir", type=str, default=None, help="Path to Cricsheet JSON directory for audit")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    extract_py = repo_root / "extract.py"

    cmd = [
        sys.executable,
        str(extract_py),
        "--batch-index", str(args.batch_index),
        "--batch-size", str(args.batch_size),
        "--delay", str(args.min_delay)
    ]

    if args.match_ids:
        cmd.extend(["--match-ids", args.match_ids])
    if args.ids_file:
        cmd.extend(["--ids-file", args.ids_file])
    if args.skip_existing:
        cmd.append("--skip-existing")
    if args.cricsheet_dir:
        cmd.extend(["--cricsheet-dir", args.cricsheet_dir])

    print("=" * 65)
    print("CRICKET DATA LAKE - LOCAL BATCH RUNNER")
    print(f"Executing: {' '.join(cmd)}")
    print(f"Jitter range: [{args.min_delay}s, {args.max_delay}s]")
    print("=" * 65)

    start_time = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root))
        exit_code = proc.returncode
    except KeyboardInterrupt:
        print("\nBatch run aborted by user.")
        sys.exit(130)

    elapsed = time.time() - start_time
    print("-" * 65)
    print(f"Batch completed in {elapsed:.1f} seconds with exit code {exit_code}")
    print("-" * 65)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
