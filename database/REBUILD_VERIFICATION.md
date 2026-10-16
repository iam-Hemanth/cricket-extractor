# Standalone rebuild verification — 16 October 2026

A fresh database built from the packaged archive passed at **475,559,615 bytes
(475.56 decimal MB)**, including all ten final materializations and their indexes.
It is below both the 480 MB preference and stricter 481 MB build-success ceiling.
The user later accepted approximately 483 MB; the builder retains the stricter gate.

## What was actually run

- macOS x86_64, PostgreSQL 18.3, Python 3.12; existing psycopg2 2.9.10 dependency.
- `database/rebuild.py fetch --archive <verified-archive> --output <new-input-dir>`
  authenticated and extracted the exact three published input files.
- `database/rebuild.py build --payload-dir <new-input-dir> --work-dir <new-canonical-root> --pg-bin <postgresql18-bin> --export-dump`
  created an entirely new private socket-only cluster. No retained backend database
  or hosted provider was changed.
- Full build finished successfully in approximately 425 seconds before dump export;
  the new server was stopped after completion. The exported custom-format dump was
  195,785,613 bytes; compressed dump size is not the database-size measurement.
- Eight archive, existing-path, implementation-integrity and strict-size-boundary
  unit tests passed. The actual connection transaction failure was reproduced, then
  corrected by rolling back the guard's validation transaction before configuring
  read-only verification. The full fresh run exercised the corrected sequence.

See [verified-build.json](verified-build.json) for input/package hashes, source counts,
full base verification flags, summary counts and actual byte measurement. The code
inventory preserves 33 backend files from commit `0239468942e8f179c4f4c67d13322412b748637b`.

## Data and checks

The import retained **5,215 verified matches**, **2,686,884 deliveries** and a
separate ledger of **18 source-review holds**. `source_full_ready=false` remains
explicit. Full delivery reconstruction, checksums, source documents, participant
rows, innings/counter parity and compact identity mappings passed verification.

All player, matchup and opposition summary rows matched independent calculations.
The five team/venue/default-card families matched direct source-derived answers.
Partnership whole-family run/ball totals reconciled; independent Python and SQL
segmentation agreed on 132 stints across six real edge-case matches. Representative
profile, comparison, alias and directional matchup checks also passed. This does
not claim an independent per-row reconstruction of every partnership.

| Final populated materialization | Rows |
| --- | ---: |
| `cricket_matchup_read_v1.batter_vs_bowler` | 278,470 |
| `cricket_opposition_read_v1.player_vs_team` | 32,914 |
| `cricket_partnership_read_v1.partnerships` | 45,769 |
| `cricket_player_read_v1.player_batting` | 20,345 |
| `cricket_player_read_v1.player_bowling` | 14,370 |
| `cricket_team_read_v1.stat_cards` | 4 |
| `cricket_team_read_v1.team_recent_matches` | 5,215 |
| `cricket_team_read_v1.team_vs_team` | 767 |
| `cricket_team_read_v1.team_vs_team_seasons` | 2,675 |
| `cricket_team_read_v1.venue_stats` | 2,246 |

## Why the new result is smaller

The earlier integrated backend measured 480,573,119 bytes. A first standalone build
on this PostgreSQL installation measured 482,465,471 bytes before index packing.
Rebuilding its 53 existing B-tree indexes at `fillfactor=100` reduced it to
475,567,807 bytes. The final independent fresh run, with that step included in the
builder, measured **475,559,615 bytes**. No records, columns, summaries, indexes or
constraints were deleted. All catalog indexes checked as valid and ready.

The schema/codec sources remain unchanged and hash-pinned. Index packing is an
explicit post-install physical layout step for the immutable snapshot. Subsequent
writes and refreshes can grow packed indexes and require temporary space; the
steady-state result is not a promise of future operating headroom.

## Scope and reproducibility limits

Another agent needs this repository, the guide and the pinned release download
(or the exact archive supplied offline), Python 3.11+ and PostgreSQL 18. No original
workstation path, sibling checkout or private `.omx` file is required. Historical
paths inside the byte-preserved manifest are informational; CLI paths locate inputs.
The archive and packaged files were checked against known historical Akamai key
values without printing those values; no matches were found in the publishable
package or payload. Existing Git history was not rewritten.

Physical PostgreSQL sizes can vary between machines. The builder measures the
completed database and rejects a result at or above 481,000,000 bytes instead of
silently dropping data or declaring an oversized build successful. Hosted restoration,
provider accounting, automatic six-hour updates, future data growth and full lake
certification require their own verification.
