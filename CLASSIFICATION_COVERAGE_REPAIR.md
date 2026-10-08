# Source classification and coverage repair — 8 October 2026

New candidates emit3.3. Both curated and unfiltered producers use the same repaired
constructor, schema and gate; collection policy, references, workflows and published
releases stay unchanged. Prior3.1/3.2 inputs remain supported as their own contracts.

## Decisions

- Classification uses source match class codes and missing/null presence, not team
  names or a league whitelist. Preserve record class, statistical family, physical
  playing format and international status independently. IPL remains a competition
  bucket after T20 class is established; Hundred retains its five-ball contract.
- HS women's general17 differs from public Statsguru selector23. The supported
  general codes are4/5/6,11/12/13 and17; unverified codes (including unsupported
  international categories) retain raw evidence and a needs-review classification.
- Explicit match/international-class/Hundred gender facts are stronger than a bad
  individual roster field. Strong conflicts become unknown; weak roster conflicts
  are recorded. With no stronger evidence, only unanimous known playing-roster
  values determine gender. Officials do not determine it.
- record_stats_eligible describes normal record-class membership, not universal
  readiness/accuracy of every statistic. Shared coverage gates and unknown gender,
  optional actor, phase and other limits still matter to an application adapter.
- Source final actual balls and exact runs are carried through consolidation.
  Unknown/mismatched source coverage is not repaired by padding or trimming balls.
- Explicit innings penalty runs are separate from deliveries and attributed only
  to the team, with unknown timing. Add only a source component not already in
  delivery extras and supported by final runs and legal-ball coverage.
- Corroborate malformed integer first-ball labels from independent over/ball/unique
  fields; preserve original markers and normalization notes. Explicit scorecard
  retired-not-out text overrides an ambiguous retired-out code, retaining the
  event and allowing the batter to return.
- Metadata-only/non-POST responses do not become completed match candidates.
  Ambiguous not-out flags, scorecard-only dismissals, unreliable running counters,
  missing actors and other contradictions remain source-review records.

## Verification before the final independent review

166 tests pass in the curated working branch; the full producer gets the same suite.
Frozen curated replay5527 sources currently yields5412 candidates and115 review
records. Nine previous rejects recover: six innings penalties, two ball labels and
one explicit retirement. Stronger gates expose previously tolerated contradictions;
these are review questions, not permission to invent facts.

Forty hash-verified full-source examples include BBL, County Championship, List A,
a tour match, women's internationals and unverified youth categories. Current
sampling accepts29; incomplete/contradictory sources and metadata-only replies are
held. This is not a certification/rebuild of the16,456-match published full lake.
Catalog/manifest keep classification and team totals, label categorical pitch-line
coverage accurately, and catalog archives include only accepted records.

## Remaining limits

No production/application migration, publication, workflow/schedule change or live
collection was tested. The complete full producer must be replayed before any
combined snapshot is released. Existing legacy classifications are unverified;
manual consolidation must not be mistaken for reclassification of old JSON files.
Non-Hundred phase labels remain legacy analysis conventions, not independently
verified historical restrictions. Source-reported actual balls can disagree with
commentary; current gates hold those cases rather than assert which source is right.
Timed-out/source-only dismissals and separately supplied special-innings collections
need explicit consumer handling; this pass does not certify their actor/timing history.
Optional non-striker gaps remain unavailable partnership coverage.

Current lifecycle/report/evidence: CricStats docs/revamp/CLASSIFICATION_REPAIR_SPEC.md,
CLASSIFICATION_REPAIR_PLAN.md and the forthcoming completion report.
