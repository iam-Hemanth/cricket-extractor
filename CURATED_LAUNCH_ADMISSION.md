# Curated launch admission — 8 October 2026

The launch uses the existing eight priority teams and flagship/Asia Cup rules,
with IPL/SA20/Hundred men/MLC. ILT20 is explicitly excluded. Afghanistan has no
all-fixture/qualifier exception; product priority does not change official class.

src/match_filter.py is the policy authority. Source-verified admission has selected,
held and excluded states; provisional discovery is checked again against actual
source class/gender/team facts. Curated extract.py now checks source admission
before commentary. The discovery script passes IDs/trophy identity to the same
rule, and writing an unfiltered delta is opt-in. The full producer is parked.

prepare_curated_launch.py is the offline launch builder. It indexes raw scorecard
metadata with hashes, detects conflicting scope facts, and resolves old CSV gaps
from source team identities. It joins source-qualified records to validated3.3
candidates, checks matching class identity, and uses the independent integrity gate.
Fresh output directories prevent old rows from leaking. Archive membership derives
only from admitted records; raw/candidate inputs are never deleted or reclassified.

The generic consolidator remains an inventory tool; use this builder for a launch
subset. Archived ILT20 remains available; updating discovery alone is insufficient.
A final selection report records every selected/held/excluded source ID and an
Afghanistan coverage list. Until that report and the app adapter are reviewed,
no source release or production swap is implied.

1298172 qualifies but remains held. Source confirms a five-ball fourth over:119
actual deliveries vs120 nominal innings/bowling scorecard balls. This is not a
missing ball. Preserve source evidence and implement an explicit actual/nominal
clock representation later; never pad/trim. Other in-scope source mismatches also
remain held and must not be described as independently proven missing data.

Current verification:203 regressions pass; initial source-backed snapshot5233 valid
matches. Final independent review, archive checks and integration are pending.
No frontend, DB, workflow, publication or full-lake code changes in this pass.
