# Cricket Extractor (Curated Lake) — Project Context for Copilot & AI Agents

> Feed this file at the start of any session working on `cricket-extractor`:
> In Chat type: `#file:CRICKET_EXTRACTOR_CONTEXT.md` then your prompt.

---

## 1. What This Repository Is

An independent, production-grade cricket data extraction, validation, and lake-building engine that fetches ball-by-ball commentary and tactical telemetry directly from **ESPNcricinfo** and outputs normalized **Match Schema v3.0 JSON**.

- **Scope**: Curated universe of **5,503 matches** (Modern Tests post-2011, ODIs post-2007, T20Is, IPL, SA20, The Hundred, ILT20, MLC).
- **Deliveries**: 2,815,230 ball-by-ball records.
- **Tactical Spatial Coverage**:
  - Wagon wheel 2D vectors: **2,247,000+ balls (79.85%)**.
  - Hawk-Eye pitch coordinates: **1,940,000+ balls (69.01%)**.
  - Shot type classifications: **2,765,000+ balls (98.40%)**.
- **Player Cross-Mapping**: 2,570+ players mapped directly to canonical Cricsheet hashes (`people.csv`) with a **90.83%** mapping rate.
- **Date Range**: February 17, 2005 – October 06, 2026.

---

## 2. Architecture & Design Decisions

### A. Two-Layer Architecture
1. **Layer 1 (Immutable Raw Responses)**: Stores unmodified raw HTTP bodies gzipped (`scorecard.json.gz`, `commentary_innN_chunkM.json.gz`, `meta.json` with SHA256 checksums). Enables 100% offline re-parsing, auditability, and algorithm evolution without re-scraping.
2. **Layer 2 (Normalized Match Schema v3.0)**: Clean, validated JSON contract (`schema/schema.json`) with strict coordinate normalization, integer overs/balls, and canonical player IDs.

### B. Akamai EdgeAuth Key Mechanism
- ESPNcricinfo's Next.js web application invokes internal consumer API endpoints (`https://hs-consumer-api.cricinfo.com/v1/pages/match/comments`) protected by Akamai EdgeAuth.
- Requires header `x-hsci-auth-token` generated using HMAC-SHA256 (`src/auth.py`).
- Key extracted from Next.js webpack bundle module 40734 (`edgeAuth.encryptionKey`): `9ced54a89687e1173e91c1f225fc02abf275a119fda8a41d731d2b04dac95ff5`.
- `scripts/refresh_akamai_key.py` automatically discovers and rotates the key if updated by ESPNcricinfo.

### C. Format Bucketing Contract
Matches are classified strictly into 5 format buckets:
- `IPL`: Competition is `'Indian Premier League'`.
- `T20I`: Men's International T20s.
- `T20`: Top domestic leagues (`SA20`, `The Hundred Men's Competition`, `International League T20`, `Major League Cricket`).
- `ODI`: One Day Internationals.
- `Test`: Test matches.

### D. Hardened Reconciliation & Validation Rules (`src/validator.py`)
- **The Hundred 5-Ball Overs**: Dynamic over length support (`balls_per_over = 5` for The Hundred, 6 otherwise).
- **Pitch Realities (5 to 8 Balls per Over)**: Completed overs tolerate 5 to 8 balls to account for umpire miscounts (e.g. Ben Laughlin 7-ball over in IPL 2018 match `1136564`) and mid-over declarations/injuries.
- **MCC Law 28.3 Helmet Penalties**: Detects 5-run jumps in running scores when ball strikes fielder helmet behind keeper and attributes `penalties = 5`.
- **Duplicate Dismissals**: Deduplicates duplicate commentary dismissal events by `player_out_id` per innings (e.g. Dean Brownlie run out entered twice in match `520592`).
- **Non-Delivery Dismissals**: Subtracts `timed_out` dismissals from delivery wickets (e.g. Angelo Mathews in 2023 World Cup match `1384429`).
- **Defensive Data Handling**: Handles `None` gracefully in `matchPlayers`, `teamPlayers`, and officials (`_umpires`, `_referee`) without raising exceptions.
- **Error Boundary**: `extract.py` catches per-match exceptions, writes to `failed_matches.json`, and allows batch loops to complete safely.

---

## 3. Releases & Distribution Packages

Release Tag: [`data-v2-batches`](https://github.com/iam-Hemanth/cricket-extractor/releases/tag/data-v2-batches)

| Asset | Size | Purpose |
| :--- | :--- | :--- |
| **`cricket_lake_v3.zip`** | **89.46 MB** | Monolithic standalone zip containing all 5,503 JSONs + `README.txt` + `matches_index.csv` + `lake_manifest.json`. |
| **`cricket_lake_v3.tar.gz`** | **73.69 MB** | Compressed tarball release archive for Linux/macOS pipelines. |
| **`matches_index.csv`** | **1.09 MB** | Machine-readable tabular index ready for Pandas (`pd.read_csv`). |
| **`README.txt`** | **0.69 MB** | Human-readable text table mapping every `<match_id>.json` file. |
| **`lake_manifest.json`** | **0.01 MB** | Complete global telemetry and dataset metrics. |
| **`matches_batch_0..23.tar.gz`** | ~74 MB total | 24 modular batch archives (Batches 0–22 historical + Batch 23 October delta). |
| **`raw_batch_0..23.tar.gz`** | ~1.1 GB total | 24 raw Layer 1 HTTP response archives. |

---

## 4. Key CLI Commands & Workflows

```bash
# 1. Run local extraction on specific matches
python extract.py --match-ids 1529230,1529229

# 2. Extract an entire manifest batch
python extract.py --manifest data/manifest.json --batch-index 0 --batch-size 250 --delay 0.2

# 3. 100% Offline re-parse from raw archives
python reparse.py --raw-dir data/raw --output-dir data/matches --strict

# 4. Consolidate matches & build manifest
python consolidate.py --input-dirs data/matches --output-dir data/master_lake/matches --manifest-path data/master_lake/lake_manifest.json

# 5. Build distribution catalog (ZIP, CSV, README.txt)
python scripts/build_lake_catalog.py --matches-dir data/master_lake/matches --output-dir data/master_lake/dist --archive-prefix cricket_lake_v3

# 6. Discover recent completed matches directly from Cricinfo
python scripts/discover_recent_matches.py --since 2026-09-18
```

---

## 5. Chronological Development Log

### Tue Oct 06 09:23:00 IST 2026
- **Architecture Specification (v8.0)**: Established 2-layer storage, hardened shell scripts (`set -euo pipefail`), upload gates (`NEW_COUNT >= PREV_COUNT`), and fact-checking schemas.

### Tue Oct 06 09:33:00 IST 2026
- **Format Bucketing Contract**: Formalized exact 5-way format mapping (`IPL`, `T20I`, `T20`, `ODI`, `Test`) matching application database queries.

### Tue Oct 06 10:05:00 IST 2026
- **Initial Repository Bootstrapping (`cricket-extractor`)**:
  - Bootstrapped `src/` modules: `client.py` (circuit breaker), `auth.py`, `commentary.py`, `constructor.py`, `validator.py`, `cricsheet_audit.py`.
  - Built `schema/schema.json`, `extract.py`, `reparse.py`.
  - Verified 6 pilot fixtures (`1415751`, `1415745`, `1384439`, `1384396`, `1389402`, `1375844`): 100% factual match against Cricsheet across all 5,110 audited deliveries.
  - Tested edge cases: `335982` (1st IPL match 2008), `1144530` (2019 WC Final tied + super over), `1415752` (2024 T20 WC DLS), `1216517` (double super over).

### Tue Oct 06 12:28:00 IST 2026
- **Akamai EdgeAuth Reverse Engineering**: Located client-side encryption key in Next.js bundle chunk (`9ced54a89687e1173e91c1f225fc02abf275a119fda8a41d731d2b04dac95ff5`) and built `scripts/refresh_akamai_key.py`.

### Tue Oct 06 13:04:00 IST 2026
- **20-Runner Parallel Matrix Extraction**: Built `.github/workflows/parallel_extract.yml` running 20 concurrent Azure VM workers across 23 batches (250 matches each).

### Tue Oct 06 15:10:00 IST 2026
- **The Hundred 5-Ball Overs Fix**: Diagnosed 201 failing Hundred matches due to 5-ball over rule. Added dynamic `balls_per_over` detection in `constructor.py` and validator. Verified match `1417790` with 0 errors.

### Tue Oct 06 15:16:00 IST 2026
- **Metadata `NoneType` Fix in Validator**: Resolved `TypeError: 'NoneType' object is not iterable` when `_referee` or `_umpires` was None.

### Tue Oct 06 16:45:00 IST 2026
- **Full Lake Extraction Audit**: All 23 batches completed (5,034 successful matches, 1.08 GB raw payloads). Categorized remaining 181 edge cases (7-ball overs, helmet penalties, sequence typos).

### Tue Oct 06 18:15:00 IST 2026
- **Hard-Case Reconciliations Applied**: Implemented MCC Law 28.3 penalty detection, duplicate dismissal deduplication, `timed_out` adjustment, and sequence ordering. Reparse tests on 11 pilot fixtures passed 11/11 (100%).

### Tue Oct 06 18:38:00 IST 2026
- **Reparse & Master Lake Consolidation**: Dispatched `reparse_and_consolidate.yml` (Run ID: `37466507557`). All 23 matrix jobs passed in 2 minutes. Consolidated **5,483 valid matches** (99.56% coverage).

### Tue Oct 06 19:16:00 IST 2026
- **Distribution Packages Built**: Created `scripts/build_lake_catalog.py`, generating `cricket_lake_v3.zip` (89.20 MB), `matches_index.csv` (1.08 MB), and `README.txt` (0.68 MB).

### Wed Oct 07 14:26:00 IST 2026
- **Cricsheet 20-Day Dormancy Audited**: Confirmed upstream Cricsheet has been frozen since September 17, 2026 (`Last-Modified: 2026-09-17`). Built direct ESPNcricinfo discovery pipeline.

### Wed Oct 07 16:42:00 IST 2026
- **Direct Cricinfo Delta Extraction**: Discovered and extracted 20 top-tier international matches from Sep 18 to Oct 6 (India vs West Indies, England vs Sri Lanka, South Africa vs Australia, Asian Games medal rounds).

### Wed Oct 07 17:08:00 IST 2026
- **Batch 23 Released**: Packaged `matches_batch_23.tar.gz` and `raw_batch_23.tar.gz` and uploaded to `data-v2-batches`.

### Wed Oct 07 17:23:00 IST 2026
- **Master Lake Refreshed**: Master consolidation completed (Run ID: `37616341448`). Published updated `cricket_lake_v3.zip` (89.46 MB), `matches_index.csv` (1.09 MB), and `README.txt` covering **5,503 matches** up to **October 06, 2026**.
