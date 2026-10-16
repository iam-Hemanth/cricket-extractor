# CricStats database build and six-hour ingestion guide

The repository contains a portable builder and the final compact-schema code for
reproducing the frozen verified snapshot. A hash-pinned release provides the input
files, so another agent does not need the original workstation or sibling checkout.
"Complete" here means all 5,215 verified matches, 2,686,884 deliveries, the 18
separate review holds and all ten final materialized summaries. It does not turn
held matches into accepted facts or mean the entire unfiltered lake.

The new standalone rebuild passed at **475,559,615 bytes (475.56 decimal MB)**
with all ten final summaries; see [the fresh verification report](database/REBUILD_VERIFICATION.md).

## Start here: reproduce the verified database

Supported environment: Linux/macOS (or Linux in WSL), Python 3.11+, PostgreSQL 18
server/client binaries, network access to the public snapshot release, and at least
10 GB free working disk. The database-size limit is not the size of the PostgreSQL
installation, staging CSVs, source files, WAL or exported dump combined.

```sh
git clone https://github.com/iam-Hemanth/cricket-extractor.git
cd cricket-extractor
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-database.txt

# Resolve the OS temporary directory (macOS /tmp is a symlink).
task_tmp=$(python -c "import pathlib,tempfile; print(pathlib.Path(tempfile.gettempdir()).resolve())")

# Both output paths must be NEW. The artifact is about 119 MB compressed;
# the three authenticated input files expand to about 1.67 GB.
python database/rebuild.py fetch --output "$task_tmp/cricstats-input-v1"
python database/rebuild.py build \
  --payload-dir "$task_tmp/cricstats-input-v1" \
  --work-dir "$task_tmp/cricstats-db-v1" \
  --pg-bin "$(pg_config --bindir)" \
  --export-dump
```

Use `python3` instead of `python3.12` if that interpreter is already version 3.11+.
If `pg_config` does not point at PostgreSQL 18, provide its actual `bin` directory
explicitly, for example `/Library/PostgreSQL/18/bin` on the original Mac. Install
PostgreSQL before running the commands. Run as an ordinary OS user, not root.
The frozen snapshot download needs no API credentials. The downloader uses Python
TLS trust or the OS certificate bundle if framework Python has none configured.

The build creates only a new user-private, socket-only PostgreSQL cluster. It
verifies input and implementation hashes, imports the complete compact base,
checks every source relation/delivery, installs all ten FINAL summary families,
rebuilds their existing B-tree indexes with snapshot packing,
runs the independent summary oracles and checks populated views/indexes. It refuses
to overwrite an existing work directory and never reads `.env` or `DATABASE_URL`.

The size preference is **below 480,000,000 bytes**. The hard success gate is
**strictly below 481,000,000 bytes**, including all ten materializations/indexes.
A larger build must fail the handoff; do not delete data or omit a summary to pass.
Physical size varies with PostgreSQL environment; the guard measures the actual
build instead of promising byte-identical storage on every machine.

Outputs inside `$task_tmp/cricstats-db-v1` (the resolved temporary directory):

- `build-report.json`: `status=passed` only after the source/native/summary checks
  and hard size gate pass; `all_ten_populated=true`; `source_full_ready=false`
  remains explicit because the 18 holds are still not admitted.
- `database.dump` with `--export-dump`: PostgreSQL custom-format dump containing
  the compact schema, original source documents, holds and final summaries.
- `data/`, `socket/`, `full.json`: the local cluster and private descriptor.
- `base-verification.json`, import/staging receipt and PostgreSQL logs.

The builder stops its new server on completion by default. Keep the verified data
files for reuse. To inspect the local database, run:

```sh
python database/rebuild.py start --work-dir "$task_tmp/cricstats-db-v1"
# Prints socket, port and database name for psql / another local client.
python database/rebuild.py stop --work-dir "$task_tmp/cricstats-db-v1"
```

Use `--leave-running` during build if a running server is desired after a successful
proof. A failed build is stopped and retained for diagnosis; a retry uses a new
work directory. Never point these proof descriptors at hosted Supabase.

`database/snapshot-lock.json` pins the archive, each input file, source manifest,
and 33 backend implementation files. `fetch --archive /path/to/archive.tar.gz`
supports an already downloaded copy with the same checks. If any hash differs,
stop; don't edit the lock merely to suppress an error.

See `database/REBUILD_VERIFICATION.md` for the independent fresh-build evidence and
scope. Six-hour incremental updates remain a separate design later in this guide.

## The short version

The database is **not the Cricket Extractor ZIP copied into PostgreSQL**. The
extractor creates versioned match JSON and source evidence; CricStats validates
that snapshot, maps it into relational records, stores ball deliveries compactly,
and calculates ten query summaries. The import is checked against source hashes
before it is treated as a valid database.

The historical final local measurement was **480,573,119 bytes (480.6 decimal MB)**, including
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

Use `prepare_curated_launch.py` for the source-backed curated selection. The generic lake
consolidator inventories/combines records; it is not the authority for curated
admission. The builder refuses an existing output folder so stale records from a
prior run cannot accidentally survive into a new archive.

The count of 5,215 matches belongs to the database proof snapshot described below.
Do not assume it equals a newer curated selection report. Always read the new
manifest and selection report before importing.

### 2. Project source JSON into database records

The source adapter (originally from `cricket-stats`, now bundled under
`database/engine/`) checks the expected
match contract, participant identities, source hashes and cricket-specific facts.
The compact importer consumes a pinned manifest and a newline-delimited match
payload; it verifies that every expected ID is present exactly once and that the
bytes match the recorded hashes. The extractor's curated ZIP is therefore an
upstream input, not itself the compact import payload.

The importer creates stable identity/dictionary maps and preserves the original
source documents and held records. Participant profile versions are stored once
and referenced from matches. Deliveries are encoded in versioned, checksummed
blocks of up to 35 consecutive deliveries. Each version-1 delivery occupies exactly
55 bytes; the database checks `payload` length equals `record_count × 55` and checks
its SHA-256. Match ID, innings number and delivery ordinal are carried outside the
record. The fixed layout keeps observed and scored components separate, uses
bounded integer fields and dictionary codes for repeated categories, and preserves
null separately from zero. Group/ball/legal-clock and code fields have declared
16-bit ranges, twelve run-component values use 8-bit fields, two tactical coordinates
use signed 16-bit fields, and review codes have a wider declared bound. Values beyond
their range fail import; changing widths or fields requires a deliberate codec
version update. In version 1, source-group value 65,535 is reserved to mean null, so
an actual source group numbered 65,535 cannot be represented; a future extract with
that value must be held until an explicit wider/versioned representation is added.
A separate SQL decoder checks exact reconstruction.

For the historical proof, the adapter created `matches.jsonl`, `checks.jsonl`,
`identity-crosswalk.json` and `verification.json`. The manifest pins each file's
hash, source-code hashes, admitted record IDs and held IDs. The compact importer
accepts the specific `d3_verified_test_subset_v1` manifest and pinned source
selection for 5,215 records plus 18 holds, marked
`full_admitted_snapshot_ready=false`. The adapter itself is pinned to the frozen
selection receipt. This exact proof package is not a general converter for any
newer or larger lake ZIP. A new snapshot needs its own reviewed selection receipt,
adapter verification and manifest; don't bypass the pinned checks to force it
through the old importer.

Use the `database/rebuild.py fetch` and `build` commands at the top of this
guide. Older backend reports used `scripts/compact_database_proof.py`; that
historical CLI is not needed or included in this standalone package. The frozen
source adapter is bundled for provenance and verification dependencies, not as
a general admission tool for arbitrary new lake records.

Never place descriptor contents or credentials in the repository, and never
pass the proof descriptor to a hosted production database.

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
replace one of the ten summaries. Verification checks the imported source rows,
delivery round trips, participant versions, source documents and holds. Summary
oracles compare all player, matchup, opposition, team, venue and default-card rows;
partnership checks compare whole-family run/ball totals plus independent stint
segmentation on six real matches and representative profile reads. Each final installer authenticates its own required indexes; the builder also rejects invalid or unready indexes across the compact and final read schemas. The 17-index count in the older all-ten storage report belongs to that earlier experiment.

### Reproduction package and final summary definitions

The earlier guide incorrectly treated the old all-ten cache experiment as the only
available builder. The final guarded installers do exist in the backend code and
are now bundled unchanged under `database/engine/`:

1. `db.player_summary.install_player_summaries`: batting and bowling.
2. `db.matchup_read.install_matchup_summaries`: directional matchup counters.
3. `db.partnership_read.install_partnership_summaries`: observed partnership stints.
4. `db.opposition_read.install_opposition_summaries`: player versus team.
5. `db.team_summary.install_team_summaries`: team pair, seasons, recent matches,
   venues and stat cards.

The orchestration installs and commits each group in dependency order, then runs
four independent oracle suites covering all ten final materializations. These use
source counters/direct calculations and observed partnership segmentation, not only
checks of view names or sizes. The final homepage materialization has four default
cards; scoped card candidates remain views. This differs from the older experiment's
9,299 candidate-card rows and 433-row venue grain.

The public frozen input archive resolves the old missing-payload problem without
removing the trusted receipt/code/payload guards. The exact original source
manifest remains byte-identical; its historical informational local path is not
used to locate inputs. CLI paths supply the authenticated downloaded files.
A newer lake ZIP is still not interchangeable with this frozen input package.

Do not run legacy `db/materialized_views.sql`, the dotenv-backed `create_views.py`,
or ignored `.omx` experiment scripts. The correct final SQL files are the bundled
`player_summary.sql`, `matchup_summary.sql`, `partnership_summary.sql`,
`opposition_summary.sql` and `team_summary.sql`. No second repository is required
for the frozen rebuild. The package preserves backend commit 02394689 code bytes;
its source inventory and snapshot lock make changes visible.

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
| Historical integrated local backend database | 480,573,119 bytes (480.6 MB) | Later private PostgreSQL measurement with all ten materialized summary families and the read service. Historical integrated measurement before the new portable rebuild. |
| New portable rebuild with all existing B-tree indexes packed | 475,559,615 bytes (475.56 MB) | Independently rebuilt from the packaged archive on PostgreSQL 18.3; all ten final summaries and all 53 existing B-tree indexes retained and verified. |

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
count above rather than presenting all experiments as one linear result. The historical
recorded size is **480.6 decimal MB (480,573,119 bytes)**, not 480.3 MB. The new
portable build measurement is recorded separately in `database/REBUILD_VERIFICATION.md`.

### Final snapshot index packing

The portable builder additionally rebuilds every existing B-tree index in the
compact and five read schemas with `fillfactor=100`. This packs the existing
index entries more tightly; it removes no source rows, columns, indexes,
constraints or materialized summaries. Index names and query keys stay intact.
The original schema/codec files remain hash-pinned and unchanged; this explicit
physical-storage step runs after installation, before independent answer checks.

This is appropriate for the immutable frozen rebuild. Future incremental writes
may split packed index pages and increase storage; refreshes also need working
space. Do not infer ingestion headroom from this steady-state snapshot size.
The builder measures `pg_database_size(current_database())` through a fresh
connection after temporary verification sessions close and fails at 481 MB or
above. The user subsequently accepted approximately 483 MB, but the builder keeps
the stricter 481 MB ceiling and below-480 MB preference to retain more room.

## Outlier handling and what the quality figures mean

The compact database size proof and the parser's cricket-data quality claims are
separate results. The database snapshot retains its own source documents and holds;
it does not prove the entire 16,456-match lake was repaired or rechecked.

| Outlier | Recorded handling | When it must stay unresolved |
| --- | --- | --- |
| The Hundred's 100-ball innings and five-ball sets | Keep `playing_format=hundred_ball`, source set/ball markers, actual legal-ball clocks and scorecard quota separately. Apply season-pinned rules; Super Five remains its own innings type. | Missing/conflicting markers, unexpected legal-ball counts, impossible sequence or source-clock mismatch fails construction or remains held. Never pad, trim or turn five-ball sets into six-ball overs. |
| Unallocated five-run penalty | The parser can detect a +5 difference between source running score and the delivery result. Store the adjustment as a penalty, not as invented batter runs. Later checks require it to reconcile with source innings totals and ball coverage. | If the score jump is not source-supported, would double-count an existing penalty, or breaks the final total, it needs review. |
| Season-specific Hundred no-ball penalty | Use the matching rule pack (2021–2025 records two penalty runs; 2026 records one) and preserve the source value and rule/version. Four evidenced historical components were normalized without changing team totals. | Unknown season/rule or contradictory source evidence remains unverified; do not apply one season's value to every Hundred match. |
| Batter timed out | Compare scorecard dismissals with delivery-attached dismissals. Keep any remaining source-only timed-out count as a separate dismissal event; it contributes a team wicket without a synthetic ball or bowler credit. | If event counts conflict or the source does not identify a batter/timing, retain the unknowns rather than invent an actor or delivery. |
| Byes/legbyes/extras mismatch | Check each component and the total independently. Keep observed source values separate from statistical attribution. Only hash-pinned historical no-ball/bye cases may move byes/legbyes into the statistical no-ball bucket; total runs, total extras and legal-ball status must stay unchanged. | Any unmatched or unexplained component remains a source-review/hold; do not redistribute an aggregate score to make it balance. |
| Duplicate dismissals, malformed ball labels, substitutions and identities | Deduplicate only evidenced duplicate events; normalize a malformed marker only when independent over/ball/sequence fields agree. Keep original XI, replacements and participation events separate, and preserve explicit aliases. | Conflicting identities, roster facts, unsupported event types or missing timing stays unknown/held. The reviewed concussion example did not contain event metadata, so no concussion event was fabricated. |

The detailed rules and tests are in [DATA_INTEGRITY_REPAIR.md](DATA_INTEGRITY_REPAIR.md),
[HUNDRED_RULES_REPAIR.md](HUNDRED_RULES_REPAIR.md),
[CLASSIFICATION_COVERAGE_REPAIR.md](CLASSIFICATION_COVERAGE_REPAIR.md) and
[CURATED_LAUNCH_ADMISSION.md](CURATED_LAUNCH_ADMISSION.md). These describe candidate
repairs and frozen-snapshot checks; the classification report says the published
full-lake release was not rebuilt or re-certified by those local repairs.

### Keep coverage and accuracy separate

The checked notes contain several different historical numbers:

- The extractor's 6 October timeline reports 5,483 valid matches and 99.56%
  coverage for that consolidation run.
- The [unfiltered-lake context](https://github.com/iam-Hemanth/cricket-lake-unfiltered/blob/main/CRICKET_LAKE_UNFILTERED_CONTEXT.md)
  reports 16,456 valid matches and 53 unreconciled edge cases, and labels the
  snapshot 99.68% "factual perfection." That is a
  historical self-reported claim, not a fresh independent audit; the same context
  warns that subsequent local repairs were not applied to the published snapshot.
- The 8 October curated admission lists 5,527 source IDs: 5,233 selected, 132 held
  and 162 excluded. The database proof's 5,215 match records plus 18 holds are a
  separate pinned database snapshot, not a new count of all missing fixtures.

The **18 source holds** are the clearest saved count that may match the remembered
review set for the compact database: they are retained in the hold ledger and left
out of imported match facts. A separate 15-match real-data cohort in the compact
store report was used to exercise edge cases (Hundred, multi-day Tests, old extras,
impact participation and timed-out/no-innings cases); it was a validation sample,
not 15 excluded matches. Other snapshots report 53 unreconciled full-lake edge cases,
115 classification-review records or 132 curated holds. None of those counts should
be substituted for another.

So the saved evidence does **not** establish a single "15 matches missing" count.
Held records are not missing fixtures, and the reported 53 edge cases are not the
same as 53 missing matches. To publish a missing-match number, first name the exact
population (dates, formats, competitions and gender), then reconcile every expected
ID to selected, held, excluded or genuinely absent. Do not label 99.56% coverage or
the 99.68% historical claim as a current certification until the full source snapshot
is replayed and independently checked.

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
| Compact block metadata | `SMALLINT` for map key, innings, start ordinal, record count and codec version | The block contract bounds these values; the importer validates them before encoding. |
| Tactical coordinates in the codec | Two signed 16-bit values inside the `BYTEA` block | Null and `(0, 0)` stay distinct; out-of-range coordinates fail instead of being rounded. |
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

## Current capacity and reproducibility scope

The historical final database was 480,573,119 bytes (480.6 decimal MB). The new
rebuild has an enforced limit strictly below 481 MB and a preferred target below
480 MB; see the fresh-build evidence for its actual measurement. A rebuild over
the hard limit fails instead of claiming success. Every verified match and all ten
final summaries are required regardless of size.

This snapshot test is not an ingestion-growth or hosted quota guarantee. A prior
refresh experiment peaked near 484.8 MB; WAL, sort temporary files and hosted
provider quota accounting require separate measurements. Do not enable automatic
updates or assume 15–20 MB growth capacity based on the frozen rebuild passing.
The existing preferred size is not a promise for a larger future dataset.

## What exists today, and what does not

As of 16 October 2026, all three GitHub Actions workflows in `cricket-extractor`
and all three in `cricket-lake-unfiltered` use manual `workflow_dispatch` triggers.
There is no six-hour scheduled workflow in either repository.

The frozen builder now runs the guarded fresh importer and final summaries from this repository. The CricStats service currently serves read requests. The importer remains a guarded fresh-database importer. A production incremental writer, correction
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

## Extract or reparse newer data

The frozen rebuild above is the reproducible size baseline. New extraction remains
available in the root repository and produces a new candidate snapshot:

```sh
python -m pip install -r requirements.txt
python extract.py --match-ids <comma-separated-source-ids> \
  --raw-dir data/raw --output-dir data/matches

# Or reparse archived raw responses entirely offline into a fresh directory.
python reparse.py --raw-dir <raw-match-directories> \
  --output-dir <new-candidate-directory> --strict --verify-determinism

# Use a fresh output directory and scorecard archives as source authority.
python prepare_curated_launch.py \
  --input-dir <candidate-directory> \
  --raw-archives <directory-containing-raw_batch_archives> \
  --output-dir <new-curated-output-directory>
```

Angle-bracket paths/IDs above are placeholders to be filled by the agent. Existing
manual batch/parallel workflows can collect the full checked-in manifest; inspect
`extract.py --help` and those workflows for batch ranges. Network extraction uses
the repository's existing secret configuration; never put its values in this guide
or the data archive. `reparse.py --strict` returns nonzero for held/failed candidates;
inspect `failed_matches.json` rather than changing rules to make the exit green.

New source receipts, per-match hashes, changed code or a larger universe must go
through a new versioned snapshot admission and payload proof before becoming a new
rebuild lock. Do not point newer JSONs at the frozen builder, change trusted hashes
blindly, or silently fill the 18 reviewed-out matches. A future six-hour writer
must implement the transactional/publication contract above; it is not activated
by this handoff.

## Handoff files

The agent needs this repository and this guide. The builder downloads the frozen
input release, or accepts its already downloaded archive. It does not need the
original workstation, a private `.omx` directory or a sibling repo.

- `database/rebuild.py`: fetch, build, start and stop CLI.
- `database/snapshot-lock.json`: exact source, archive and implementation hashes,
  expected 5,215 matches / 2,686,884 deliveries / 18 holds and size limits.
- `database/snapshots/verified-subset-manifest.json`: original immutable import authority.
- `database/engine/`: byte-preserved backend modules and five final SQL groups.
- `requirements-database.txt`: the existing PostgreSQL driver used by the builder.
- `database/tests/test_rebuild.py`: meaningful archive, target and budget guards.
- `database/REBUILD_VERIFICATION.md`: fresh full-data reconstruction evidence.
- `CRICKET_EXTRACTOR_CONTEXT.md`, `CURATED_LAUNCH_ADMISSION.md`, repair reports and
  root `extract.py`/`reparse.py`/`prepare_curated_launch.py`: source collection and
  candidate curation.

Historical storage measurements in this guide come from the original CricStats
compact-store/all-ten/backend service reports. The new reproducibility report must
identify the exact package and build actually checked, rather than treating those
historical reports alone as a new end-to-end test.
