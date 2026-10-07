# Data integrity repair — schema 3.1 candidate

Updated: 7 October 2026. These are local reviewed repairs; published releases and
the CricStats website have not been changed.

## What changed

- Team wicket counts exclude retired-hurt/not-out events. Event counts remain
  separate, and unknown dismissal kinds require review without needing a scorecard.
- Run components are checked independently. Four evidenced 2021/2023 Hundred
  no-ball components are normalized from one to two penalty runs without changing
  team totals. Source values and rule identifiers are retained in normalization notes.
- Schema 3.1 adds a match-level players registry, independent original-XI status,
  and explicit participation_events. More than eleven contributors is permitted.
- Internal and public ESPN IDs are separate. Explicit mappings cover delivery,
  dismissal, and fielder identities; unresolved supplied identities are reported.
- Original starters, replacements, and substitute fielders retain their own stats.
  Unused nominated options do not activate. Missing replacement timing/reason is
  unknown. An updated active eleven is not automatically an original starting XI.
- The archived IPL PLAYER_REPLACEMENT shape is supported; source code 8 within IPL
  is evidenced as an Impact event. Other unsupported source types remain unknown.
  The reviewed concussion fixture lacks event metadata, so no event is fabricated.
- Extraction, offline reparse, and consolidation share schema/identity/scoring gates.
  Metrics exclude officials, distinguish team wickets from events, label pitch-line
  categories accurately, and report contained schema versions.

## Verify locally

Use an environment with this repository's existing requirements installed:

    python -B -m unittest discover -s tests -v

Offline reconstruction (use separate output storage):

    python -B reparse.py --raw-dir PATH_TO_ARCHIVED_RAW --output-dir NEW_CANDIDATE_DIR --strict --verify-determinism

Do not point the output at published/source files. Requested missing fixtures and
rejected candidates produce a failure report; strict mode returns nonzero.
The jsonschema validator is already declared in requirements.txt; no project
dependency was added. Fixtures include numeric source evidence, not credentials.

## What is still outside this pass

Classification/gender scope and competition phase semantics, broader scorecard
tolerance explanations, source-unknown identities, cross-source mapping accuracy,
six-hour operation, correction/retry tracking, recent packages, and the application
adapter remain separate work. Passing these tests does not mean every source fact
is correct or the published dataset is ready for production migration.

Existing schema 3.0 snapshots remain unchanged. Rebuild a separate 3.1 candidate
from frozen raw/reference inputs, explain every rejection, and review the new
consumer contract before integrating. Do not silently relabel old match files.

The associated project lifecycle/spec/decision history is maintained in the
cricket-stats repository under docs/revamp. Start from its STATUS.md when resuming
the complete data transition and frontend revamp.
