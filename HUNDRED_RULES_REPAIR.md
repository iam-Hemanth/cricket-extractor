# Hundred candidate repair — 8 October 2026

The Hundred now emits Match JSON 3.2.0; other formats retain 3.1.0.
This is a source-parser change, not an application migration or publication.

## Contract

- Retain T20 statistical classification, with playing_format=hundred_ball and
  source format/general/international codes. Display unit is balls. Economy
  basis is six balls, independently of five-ball set display.
- Pin verified core rule packs for 2021–2026 to official ECB URL and PDF SHA256.
  No-ball penalties and points come from that season. Season/date disagreement
  or an unsupported competition/season stays unverified.
- Preserve delivery markers as strings and source over/legal-ball markers.
  Store every-delivery sequence and legal-ball counts before/after each event.
  Source ballsUnique is the every-delivery counter; ballsActual points to the next
  legal position being attempted, including on illegal deliveries. Do not compare
  it with completed legal_balls_after on wides/no-balls.
- Store source totalBalls separately from actual legal balls. Full 100-ball
  innings use nominal conditions-based phases: powerplay 1–25, middle 26–75,
  death 76–100. Death is a declared analytical policy, not an official law.
- Reduced, absent or conflicting allocations have phase=unknown. Where possible
  nominal_phase and nominal reduced boundaries are available separately; these
  must not be presented as historical fielding restrictions. Final quota alone
  cannot reconstruct a rain interruption's earlier restrictions.
- Keep Super Five as innings_kind=super_five, neutral phase and excluded from
  standard career statistics by include_in_standard_stats=false. Data-inventory
  manifest totals still count all stored events; they are not career statistics.
- Five-ball sets must have five legal deliveries except a final partial set.
  Miscounts are review errors, never silently resampled. Shared validation checks
  clocks, derived metadata/phases, allocations, absolute main-innings 20-ball bowler limits,
  ten consecutive legal balls, partial-set appearances (four total/two consecutive),
  and Super Five five-ball/two-wicket limits. No deliveries may follow the terminal
  ball/dismissal. Actual source scorecard balls and available provider counters are
  reconciled, and counter coverage is explicit; recomputed clocks cannot hide gaps.
- Missing or malformed Hundred delivery markers fail construction. The shared
  schema/identity/scoring gates and consolidation support both 3.1 and 3.2.

## Evidence and limitations

After the independent branch review and test-first fixes, 113 regressions passed, including two
projected real raw fixtures with original compressed-payload hashes. Final replay
of all 201 previous accepted Hundred matches was deterministic and scorecard
legal-ball totals reconciled. Runs, extras, wickets, actors, rosters, replacements,
results and source markers stayed unchanged. Full replay evidence is in CricStats
at docs/revamp/evidence/2026-10-08/hundred (see final report for post-review results).

Reduced bowler quotas are not certified/enforced from final allocation alone;
interruption timing can change permissible earlier use. These innings retain
review status. No actual Super Five or non-final miscount occurred in the 201-match
archive cohort; those failure cases have synthetic regression coverage. The source
may not expose every umpire/field-restriction decision; nominal phase provenance
must remain visible in any future adapter.

The public 2022 document has stale 2021 references; 2024/2025 have multiple revisions.
The core clauses used here agree across reviewed revisions; this is not a complete
law engine or certification of other clauses. Unfiltered-lake producers, website
SQL/display, standings NRR, broader classification/gender and scheduling remain
separate work. Do not publish a mixed lake until consumers understand this version.

## Independent review disposition

One fresh reviewer found four Important gaps: omissions could shift clocks,
post-terminal events could pass, reduced/missing allocation disabled the absolute
bowler ceiling, and partial sets bypassed appearance limits. Nine regression tests
(including a real missing-set fixture and missing/reduced allocation subcases)
reproduced those failures; all were fixed and the full 113-test suite is green.
The stricter archive replay and standard offline CLI still accept all 201 matches.
No Critical or Minor findings; no repeat reviewer run after the fixes.
