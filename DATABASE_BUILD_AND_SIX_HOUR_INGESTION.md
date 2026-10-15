# CricStats database build and six-hour ingestion guide

This guide explains the measured local database built from a pinned Cricket
Extractor data snapshot, and the work required before future extracts can flow
into it every six hours. It is a handoff for the next coding session. The schedule
and incremental-ingestion path below are a design, not a currently running job.

## The short version

The database is **not the Cricket Extractor ZIP copied into PostgreSQL**. The
extractor creates versioned match JSON and source evidence; CricStats validates
that snapshot, maps it into relational records, stores ball deliveries compactly,
and calculates ten query summaries. The import is checked against source hashes
before it is treated as a valid database.

The final local measurement was **480,573,119 bytes (480.6 decimal MB)**, including
all ten materialized summaries. This was a private local PostgreSQL 18 database,
not a hosted Supabase database or a production migration. Its snapshot contained
5,215 matches, 2,686,884 deliveries and 18 separately retained source holds. The
source report still says `full_ready=false`; those counts do not mean every possible
match or every held record is ready for public statistics.

## What was built

### 1. Extract and curate a fixed source snapshot

In `cricket-extractor`, the pipeline is:

1. `extract.py` obtains source material and stores raw responses for later review.
2. `reparse.py` turns those responses into versioned match JSON.
3. `prepare_curated_launch.py` checks candidate matches against source scorecards,
   the current scope policy and the integrity checks. It writes a fresh curated
   output, a selection report, a source-scope index and a curated archive. Selected,
   held and excluded records remain distinct.
4. The source manifest and file hashes pin the exact snapshot. A later corrected
   match or changed policy creates a new snapshot; it must not silently alter the
   evidence for an earlier import.

Use `prepare_curated_launch.py` for a product-ready subset. The generic lake
consolidator inventories/combines records; it is not the authority for curated
admission. The builder refuses an existing output folder so stale records from a
prior run cannot accidentally survive into a new archive.

The count of 5,215 matches belongs to the database proof snapshot described below.
Do not assume it equals a newer curated selection report. Always read the new
manifest and selection report before importing.

### 2. Project source JSON into database records

In the sibling `cricket-stats` repository, the source adapter checks the expected
match contract, participant identities, source hashes and cricket-specific facts.
The compact importer consumes a pinned manifest and a newline-delimited match
payload; it verifies that every expected ID is present exactly once and that the
bytes match the recorded hashes. The extractor's curated ZIP is therefore an
upstream input, not itself the compact import payload.

The importer creates stable identity/dictionary maps and preserves the original
source documents and held records. Participant profile versions are stored once
and referenced from matches. Deliveries are encoded in versioned, checksummed
blocks (up to 35 consecutive deliveries per block); a separate SQL decoder checks
that the compact representation reconstructs the source values exactly. The
storage work did not sample matches, round statistics or discard the extra
source fields needed to rebuild answers.

The fresh proof importer intentionally refuses to load into a populated database.
It is for a new, explicitly identified proof database; it is not the six-hour
update mechanism.

### 3. Add and verify all ten stored summaries

The compact base database measured **444,085,951 bytes** before the ten consumer
summaries. The final database measurement includes all ten materialized summary
families and their helper indexes:

1. Player batting
2. Player bowling
3. Batter versus bowler
4. Player versus team
5. Venue statistics
6. Partnerships
7. Team versus team
8. Team versus team by season
9. Recent team matches
10. Stat card candidates

The participant compatibility output is reconstructed with a join; it did not
replace one of the ten summaries. Verification checked the imported source rows,
delivery round trips, participant versions, source documents, holds and the result
rows from every summary. The report records 17 valid helper indexes.

## Why it took several attempts to reach the final size

The figures below come from different build stages. They explain the experiments;
they are not one continuous subtraction where each line can be added to the next.

| Attempt | Measured size | What changed or was learned |
| --- | ---: | --- |
| Original full relational proof database | 1,516,320,447 bytes (1.52 GB) | Deliveries and indexes used about 1.23 GB; tactical JSON alone represented about 507 MB before table overhead. |
| First compact-storage prototype | 469,931,711 bytes (469.9 MB) | Kept source documents and current data, but did not yet include replacements for all ten legacy materialized views. It proved a compact layout, not the final application budget. |
| Early all-ten layout | 513,865,407 bytes (513.9 MB) | Keeping all ten stored summaries in that layout pushed it over the target. |
| Compression-only control restore | 515,716,799 bytes (515.7 MB) | Changing PostgreSQL compression alone did not solve the storage problem. |
| Share repeated participant profiles | 479,057,599 bytes (479.1 MB) | Stored 2,504 exact participant profile versions once and referenced them from match participants. Match-specific roles, teams and references remained attached to each match. |
| Narrow selected summary counters | 470,496,959 bytes (470.5 MB) | Used smaller integer storage only after checking every current value fit, then compared answers exactly. No rounding or row removal. |
| Refresh and verify all ten summaries | 471,258,815 bytes (471.3 MB) | All ten summaries were populated again and their answers/indexes checked. This was still a storage experiment, not the final integrated database. |
| Fresh compact-store base import | 444,085,951 bytes (444.1 MB) | Later compact schema/import proof, measured before the ten consumer summaries were included. |
| Final integrated local backend database | 480,573,119 bytes (480.6 MB) | Later private PostgreSQL measurement with all ten materialized summary families and the read service. This is the final size quoted in this guide. |

Two choices saved most space without removing current facts: encode each delivery in
a small versioned block and dictionary-encode repeated categories; store identical
participant profile versions once while keeping distinct historical versions. We
rejected whole tactical-record deduplication because most combinations were unique,
and rejected compression settings as a sufficient fix. Counter narrowing was limited
to summary columns with proven bounds; future imports must retain range checks and
fail safely if a value outgrows those bounds.

The early 469.9 MB prototype and 471.3 MB all-ten experiment are useful proof, but
they are not the final integrated 480.6 MB database. Physical packing, the later
fresh schema/import path and the integration stage differ. Use the exact final byte
count above rather than presenting all experiments as one linear result. The final
recorded size is **480.6 decimal MB (480,573,119 bytes)**, not 480.3 MB.

## Schema types that mattered

The storage win did **not** come from changing every column to the smallest type.
The compact schema uses different types according to the meaning and safe range of
each value:

| Data | Type used in the compact base schema | Reason and guard |
| --- | --- | --- |
| External match ID | `TEXT` | Preserve the source ID exactly; validate that it contains the expected digits. Do not assume future provider IDs fit a small integer. |
| External player ID | `BIGINT` | Preserve the provider's numeric identity with headroom; it is not the same as a compact internal key. |
| Internal match/player map keys | `SMALLINT` | The verified snapshot used 5,215 matches and 2,493 players, within signed `SMALLINT` range (−32,768 to 32,767). The schema checks positive keys and the importer refuses overflow. |
| Innings numbers, source counters and participant profile keys | `INTEGER` | Small in this snapshot, but retained as ordinary integers in the proof schema; the build did not shrink every plausible-looking column. |
| Compact block keys and coordinates | `SMALLINT` for map key, innings, start ordinal, record count and codec version | The block contract bounds these values; the importer validates them before encoding. |
| Actor-inning aggregates | `SMALLINT` for role/player/match/innings keys; `INTEGER` for runs, balls and dismissals | Narrow the repeated join keys; retain wider exact counters for totals. |
| Dictionary IDs | `BIGINT` with explicit per-dictionary range checks | Most dictionaries cap at 32,767; review codes may use values up to 4,294,967,295. |
| Match dates | `DATE` | Store calendar dates as dates rather than repeated text. |
| Match metadata, outcomes and source facts | `JSONB` | Preserve structured values and their types for reconstruction and queries. |
| Original compressed source documents | `BYTEA` plus SHA-256 | Keep the exact compressed source bytes and verify them. |
| Compact delivery blocks | `BYTEA` payload and checksum | Keep the versioned packed delivery bytes intact; blocks are bounded and checksummed. |
| Query rates and averages | `NUMERIC` expressions in SQL | Compute from exact counters and denominators; do not use floating-point approximations to save space. |

The separate all-ten experiment narrowed **only the `batter_vs_bowler`
materialized summary** after checking the minimum and maximum of every column fit
signed `SMALLINT`; the largest noted matchup ball counter was 510. It rebuilt the
summary, retained its indexes, and compared the values exactly with the original
result. This is why the guide lists that attempt as a measured optimization. It is
not a blanket instruction to convert all run, ball, ID or event columns to
`SMALLINT`; for example, actor-inning run/ball/dismissal counters in the compact
base schema remain `INTEGER`, and source provider IDs remain wider.

### Safe type-selection rule for future schema work

First define what a field means and its valid contract range, including corrections,
future imports and edge cases. Then choose a native type that covers that full range,
add a database check, and test minimum/maximum values. Only narrow a populated
summary after measuring every column, rebuilding it, and proving exact value parity
and refresh behavior. On overflow, reject or plan a versioned migration; never clip,
round or silently wrap a value. For the space target, the measured high-volume
delivery encoding and sharing repeated profile versions mattered more than changing
small metadata columns.

## Current capacity warning

The working limit has been 485 MB. At 480.6 MB, the final database has only about
4.4 MB of steady space below that limit. A prior all-ten refresh test briefly
measured 484.8 MB on an unchanged snapshot. That means the apparent spare space
does not establish safe room for 15–20 MB of new matches, correction imports or
refresh work. The local measurements also do not prove how Supabase counts its
storage, WAL or temporary files.

**Do not turn on automatic ingestion at the current size.** First reduce or raise
the usable database capacity, then measure a representative import, correction,
all-ten refresh and rollback on the actual hosting plan. The 485 MB figure is the
project's chosen operating ceiling, not a guarantee from the provider.

## What exists today, and what does not

As of 15 October 2026, all three GitHub Actions workflows in `cricket-extractor`
and all three in `cricket-lake-unfiltered` use manual `workflow_dispatch` triggers.
There is no six-hour scheduled workflow in either repository.

The CricStats service currently serves read requests. Its compact proof importer
is a guarded fresh-database importer. A production incremental writer, correction
publication, hosted scheduling, safe refresh of one coherent generation across
all ten summaries, and tested hosted recovery are still required. Adding a cron
line alone would not safely ingest data.

## Recommended six-hour flow

Use one serialized pipeline. Prefer a small delta package for each run over
rebuilding and transferring the complete lake every six hours.

```text
scheduled discovery and extraction
    -> curated delta + manifest and hashes
    -> private CricStats importer validates the whole delta
    -> transaction writes new match revisions
    -> summaries refresh and one verified generation is published
    -> verify capacity and results, then advance the success checkpoint
```

### Extractor job

- Add a scheduled trigger, for example `17 */6 * * *` (four times per day in
  GitHub Actions' UTC clock), while keeping a manual trigger for recovery. GitHub
  scheduled runs can be delayed, so the importer must be safe to retry and the
  schedule is not an exact six-hour service guarantee.
- Prevent overlapping runs. Discover matches since the last **successfully
  committed** source watermark, with an overlap window so late results and source
  corrections are noticed. Do not advance the checkpoint merely because extraction
  started or a workflow ended green before database verification.
- Re-run the normal source-scope, identity, schema and cricket-integrity checks.
  Put uncertain, incomplete or contradictory records into the hold/failure report;
  never convert missing information into a zero or publish a held record as a
  verified statistic.
- Produce an immutable per-run delta containing selected match JSON, raw/source
  document references or bytes as required, a manifest, schema/policy/extractor
  versions, source revision hashes, before/after watermarks, and explicit selected,
  held and failed counts. Keep credentials out of the archive, Markdown, Git
  history and job logs.
- Store the run package in private artifact/object storage. Do not commit match
  data, database connection strings or extracted key values to the repository.

### CricStats importer and publication

- Add a separate incremental importer in `cricket-stats`. It should accept a
  manifest and one immutable run package, verify every digest and scope decision,
  and reject missing, extra, duplicate or contradictory records before writing.
- Make retries idempotent by match ID plus source revision/hash. Replaying a
  successful package must not double-count deliveries. A correction replaces one
  complete verified match revision in a transaction while retaining its prior
  provenance and source document. Keep identity dictionaries append-only and
  profile metadata versioned; never renumber existing dictionary IDs.
- Stage and validate the complete delta, then commit it atomically. Refresh the
  affected data summaries only through a defined publication protocol so readers
  cannot mix old and new answers across the ten summaries. A concurrent refresh
  of a single view is not an atomic publication of all ten.
- After commit, verify source/match/delivery counts, hold counts, summary freshness,
  row-level checks and current/peak database size. Advance the successful watermark
  only after this verification. If any stage fails, keep the prior published
  generation and checkpoint; retry the same immutable run or issue a correction.
- Serialize ingestion and maintenance. Keep a tested backup/restore route before
  the first hosted update. Test a normal delta, a corrected existing match, a
  duplicate retry, a failed/held record and a refresh interruption before enabling
  the schedule.

## Handoff map

`cricket-extractor` owns source discovery, extraction, parsing, curation and the
immutable delta manifest. The sibling `cricket-stats` repository owns source
adaptation, the compact PostgreSQL schema/importer, query summaries and the API.
The extractor must not connect directly to the database. The boundary between the
repositories is the versioned, hash-checked run package and a private authenticated
handoff.

Useful files in `cricket-extractor`:

- `CRICKET_EXTRACTOR_CONTEXT.md` — extraction and lake commands.
- `CURATED_LAUNCH_ADMISSION.md` — scope rules and current frozen-corpus accounting.
- `prepare_curated_launch.py` — offline source-backed curated builder.
- `.github/workflows/reparse_and_consolidate.yml` — current manual reparse/build flow.
- `.github/workflows/parallel_extract.yml` and `batch_extract.yml` — current manual
  extraction flows.

Useful code in sibling `cricket-stats`:

- `ingestion/source_adapter.py` and `ingestion/source_store.py` — source projection
  and pinned import payload checks.
- `ingestion/compact_codec.py`, `ingestion/compact_store.py`,
  `db/compact_schema.sql` and `db/compact_verify.py` — compact fresh import and
  proof. Do not reuse the fresh importer as an incremental updater.
- `db/materialized_views.sql` — consumer summary definitions.
- `api/backend.py` and `db/runtime_connection.py` — current read-service boundary.

The measured evidence lives in the sibling repository's
`docs/revamp/D3_COMPACT_STORE_REPORT.md`,
`docs/revamp/D3_ALL_MATERIALIZED_STORAGE_REPORT.md` and
`docs/revamp/BACKEND_SERVICE_REPORT.md`. Treat their reported counts and byte sizes
as snapshot-specific proof, not as a claim that six-hour ingestion or hosted
refresh has already been implemented.
