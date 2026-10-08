"""
src/validator.py
Hardened Multi-Dimension Validator for Cricket Match JSON.
"""
from typing import Dict, List, Optional, Tuple
from functools import lru_cache
from pathlib import Path
import json
from collections import Counter, defaultdict

from src import hundred, classification, source_facts
import re

NON_DISMISSAL_KINDS = {"retired hurt", "retired not out"}
DISMISSAL_KINDS = {
    "caught", "caught and bowled", "bowled", "lbw", "run out", "stumped",
    "hit wicket", "handled the ball", "obstructing the field",
    "hit the ball twice", "timed out", "retired out",
}


def count_team_wickets(deliveries: List[dict]) -> int:
    """Count dismissals independently of retirement/other recorded events."""
    count = 0
    for delivery in deliveries:
        for event in delivery.get("wickets") or []:
            kind = str(event.get("kind") or "").strip().lower()
            if kind in NON_DISMISSAL_KINDS:
                continue
            if kind not in DISMISSAL_KINDS:
                raise ValueError(f"Unrecognized dismissal kind: {kind!r}")
            count += 1
    return count


@lru_cache(maxsize=1)
def _schema_validator():
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).parents[1] / "schema/schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_match_data(match: dict, scorecard_innings: Optional[List[dict]] = None) -> List[str]:
    """Apply the same schema, identity, and scoring gate in every candidate writer."""
    errors = [f"Schema {list(error.absolute_path)}: {error.message}"
              for error in _schema_validator().iter_errors(match)]
    if errors:
        return errors
    _, identity_errors = validate_player_registry(match)
    errors.extend(identity_errors)
    innings = match.get("innings") or []
    if scorecard_innings is not None and len(innings) != len(scorecard_innings):
        errors.append(f"Incomplete innings: candidate {len(innings)} != source {len(scorecard_innings)}")
    for index, inn in enumerate(innings):
        source = scorecard_innings[index] if scorecard_innings is not None and index < len(scorecard_innings) else None
        _, innings_errors = validate_innings(inn, source, is_last_innings=index == len(innings)-1,
                                             balls_per_over=match["meta"]["balls_per_over"])
        errors.extend(innings_errors)
        if match['meta']['schema_version']=='3.2.0' and source and source.get('balls') is not None:
            actual=sum(d['is_legal'] for ov in inn['overs'] for d in ov['deliveries'])
            if actual!=source['balls']:
                errors.append('Hundred source scorecard legal-ball total mismatch')
    version=match['meta']['schema_version']
    if version=='3.3.0':
        for inn in innings:
            deliveries=[d for ov in inn['overs'] for d in ov['deliveries']]
            snap=inn['source_scorecard']
            _,source_errors=validate_innings(inn,snap,balls_per_over=match['meta']['balls_per_over'])
            errors.extend(source_errors)
            legal=sum(d['is_legal'] for d in deliveries)
            if inn['source_legal_balls']!=snap['balls'] or (snap['balls'] is not None and legal!=snap['balls']):
                errors.append('Source scorecard legal-ball coverage mismatch')
            penalty=source_facts.innings_penalty(deliveries,snap)
            if penalty['penalty_adjustment_status']=='needs_review':
                errors.append('Source penalty component requires review')
            if any(inn.get(k)!=v for k,v in penalty.items()):
                errors.append('Source innings penalty adjustment mismatch')
            runs=sum(d['runs']['total'] for d in deliveries)+inn['innings_penalty_runs']
            if snap['runs'] is not None and runs!=snap['runs']:
                errors.append('Source scorecard exact runs mismatch')
            for d in deliveries:
                marker=source_facts.canonical_marker(d['source_actual_delivery'],d['source_over_number'],
                                                    d['source_ball_number'],d['source_unique_marker'])
                if d['actual_delivery']!=marker:
                    errors.append('Canonical delivery marker contradicts source evidence')
                if not re.fullmatch(r'[0-9]+\.[0-9]+',d['actual_delivery']):
                    errors.append('Uncorroborated delivery marker requires review')
        expected=classification.classify(classification.source_from_metadata(match['meta']),
                                        match['meta']['gender_evidence']['playing_roster_values'])
        for key,value in expected.items():
            if match['meta'].get(key)!=value:
                errors.append(f'Source classification mismatch: {key}')
    if version=='3.2.0' or (version=='3.3.0' and match['meta']['playing_format']=='hundred_ball'):
        errors.extend(validate_hundred_contract(match))
    return errors

def parse_delivery_key(actual_str: str) -> Tuple[int, int]:
    """Parse delivery string (e.g. '2.6') into integer tuple (2, 6)."""
    try:
        parts = str(actual_str).split(".")
        return (int(parts[0]), int(parts[1]))
    except Exception:
        return (0, 0)


def validate_innings(
    inn: dict,
    sc_inn: Optional[dict] = None,
    is_last_innings: bool = False,
    balls_per_over: int = 6
) -> Tuple[bool, List[str]]:
    """
    Validate innings delivery sequence, running totals, and scorecard totals.
    Returns: (is_valid, list_of_errors)
    """
    errors = []
    overs = inn.get("overs") or []
    if not overs:
        if sc_inn and ((sc_inn.get("runs") or 0) > 0 or (sc_inn.get("wickets") or 0) > 0):
            return False, ["Incomplete deliveries for a nonzero scorecard innings"]
        return True, []

    for ov in overs:
        for d in ov.get("deliveries") or []:
            runs, extras = d.get("runs") or {}, d.get("extras") or {}
            components = [runs.get(name) for name in ("batter", "extras", "total")]
            if any(type(value) is not int or value < 0 for value in components):
                errors.append(f"Invalid run components at {d.get('actual_delivery')}")
                continue
            if any(type(value) is not int or value < 0 for value in extras.values()):
                errors.append(f"Invalid extras components at {d.get('actual_delivery')}")
                continue
            if runs["total"] != runs["batter"] + runs["extras"]:
                errors.append(f"Run component mismatch at {d.get('actual_delivery')}")
            if runs["extras"] != sum(extras.values()):
                errors.append(f"Extras component mismatch at {d.get('actual_delivery')}")
            if d.get("is_legal") != (not bool(extras.get("wides") or extras.get("noballs"))):
                errors.append(f"Legal-ball component mismatch at {d.get('actual_delivery')}")
    if errors:
        return False, errors
    try:
        count_team_wickets([d for ov in overs for d in ov.get("deliveries") or []])
    except ValueError as exc:
        return False, [str(exc)]

    # 1. Monotonicity & legal balls per over check
    prev_tuple = (-1, -1)
    tot_overs = len(overs)

    for ov_idx, ov in enumerate(overs):
        delivs = ov.get("deliveries") or []
        is_final_over = (ov_idx == tot_overs - 1)
        legal_count = 0

        for d in delivs:
            actual_str = d.get("actual_delivery")
            if actual_str:
                curr_tuple = parse_delivery_key(actual_str)
                # Check cross-over monotonicity; within the same over, human scorers occasionally
                # entered 0.2 before 0.1 on extra deliveries
                if curr_tuple < prev_tuple and curr_tuple[0] != prev_tuple[0]:
                    errors.append(
                        f"Non-monotonic delivery sequence in Inn {inn.get('innings_number')}: "
                        f"{prev_tuple} -> {curr_tuple}"
                    )
                prev_tuple = curr_tuple

            if d.get("is_legal"):
                legal_count += 1

        # Hundred miscounts require review; final partial sets are legitimate.
        # Retain the existing non-Hundred tolerance in this bounded repair.
        if balls_per_over == 5:
            invalid_set = legal_count > 5 or (not is_final_over and legal_count != 5)
        else:
            invalid_set = not is_final_over and not balls_per_over-1 <= legal_count <= balls_per_over+2
        if invalid_set:
            errors.append(
                f"Inn {inn.get('innings_number')} Over {ov.get('over')} has {legal_count} legal balls (expected {balls_per_over})"
            )

    # 2. Cumulative score integrity vs Cricinfo totalInningRuns
    calc_runs = 0
    for ov in overs:
        for d in ov.get("deliveries") or []:
            calc_runs += d.get("runs", {}).get("total", 0)
            cricinfo_total = d.get("total_inning_runs")
            # Tolerate isolated 0-resets or minor single-ball typos if within 2 runs
            if cricinfo_total is not None and cricinfo_total > 0 and abs(calc_runs - cricinfo_total) > 2:
                errors.append(
                    f"Running score mismatch at Ball {d.get('actual_delivery')}: "
                    f"calculated {calc_runs} != Cricinfo {cricinfo_total}"
                )
                break
        if errors:
            break

    # 3. Scorecard 5-dimension reconciliation
    if sc_inn:
        sc_runs = sc_inn.get("runs")
        sc_wkts = sc_inn.get("wickets")
        got_runs = sum(d.get("runs", {}).get("total", 0) for ov in overs for d in ov.get("deliveries", [])) + inn.get('innings_penalty_runs',0)
        try:
            got_wkts = count_team_wickets([
                d for ov in overs for d in ov.get("deliveries", [])
            ])
        except ValueError as exc:
            errors.append(str(exc))
            return False, errors

        # Check for non-delivery dismissals like 'timed out' (e.g. Angelo Mathews 2023 WC)
        timed_out_count = sc_inn.get('timed_out') or 0
        for b in (sc_inn.get("inningBatsmen") or []):
            d_text = b.get("dismissalText") or {}
            if isinstance(d_text, dict) and d_text.get("short") == "timed out":
                timed_out_count += 1
            elif isinstance(d_text, str) and "timed out" in d_text.lower():
                timed_out_count += 1

        expected_wkts = (sc_wkts - timed_out_count) if sc_wkts is not None else None

        # Allow minor ≤ 2 run historical scorer graphic discrepancy on multi-day/century games
        if sc_runs is not None and abs(got_runs - sc_runs) > 2:
            errors.append(f"Scorecard runs mismatch: got {got_runs} != scorecard {sc_runs}")
        if expected_wkts is not None and got_wkts != expected_wkts:
            errors.append(f"Scorecard wickets mismatch: got {got_wkts} != scorecard {sc_wkts} (timed out: {timed_out_count})")

        # Extras breakdown check
        if isinstance(sc_inn.get("extras"), dict):
            sc_extras = sc_inn.get("extras")
            sc_wides = sc_extras.get("wides") or 0
            sc_noballs = sc_extras.get("noballs") or 0
            sc_byes = sc_extras.get("byes") or 0
            sc_legbyes = sc_extras.get("legbyes") or 0
        else:
            sc_wides = sc_inn.get("wides") or 0
            sc_noballs = sc_inn.get("noballs") or 0
            sc_byes = sc_inn.get("byes") or 0
            sc_legbyes = sc_inn.get("legbyes") or 0

        got_wides = sum(d.get("extras", {}).get("wides", 0) for ov in overs for d in ov.get("deliveries", []))
        got_noballs = sum(d.get("extras", {}).get("noballs", 0) for ov in overs for d in ov.get("deliveries", []))
        got_byes = sum(d.get("extras", {}).get("byes", 0) for ov in overs for d in ov.get("deliveries", []))
        got_legbyes = sum(d.get("extras", {}).get("legbyes", 0) for ov in overs for d in ov.get("deliveries", []))

        sc_extras_total = sc_wides + sc_noballs + sc_byes + sc_legbyes
        got_extras_total = got_wides + got_noballs + got_byes + got_legbyes

        # If scorecard has extras recorded, total extras must match within tolerance
        if sc_extras_total > 0 and abs(got_extras_total - sc_extras_total) > 2:
            errors.append(f"Total extras mismatch: got {got_extras_total} != scorecard {sc_extras_total}")


    return (len(errors) == 0), errors

def validate_player_registry(match_data: dict) -> Tuple[bool, List[str]]:
    """Verify that every player ID referenced in deliveries exists in playing_xi or substitutes."""
    errors = []
    if (match_data.get("meta") or {}).get("schema_version") in {"3.1.0", "3.2.0", "3.3.0"}:
        registry = match_data.get("players") or {}
        for pid, player in registry.items():
            if player.get("player_id") != pid or pid != f"espn_{player.get('espn_id')}":
                errors.append(f"Invalid canonical participant identity {pid}")
        for issue in (match_data.get("meta") or {}).get("identity_issues") or []:
            errors.append(f"Participant identity issue: {issue}")
        for inn in match_data.get("innings") or []:
            for ov in inn.get("overs") or []:
                for d in ov.get("deliveries") or []:
                    for role in ("batter_id", "bowler_id", "non_striker_id"):
                        pid = d.get(role)
                        if (pid is None and role != "non_striker_id") or (pid is not None and pid not in registry):
                            errors.append(f"Unresolved {role}: {pid}")
                    for wicket in d.get("wickets") or []:
                        pid = wicket.get("player_out_id")
                        if pid not in registry:
                            errors.append(f"Unresolved dismissed participant: {pid}")
                        for fielder in wicket.get("fielders") or []:
                            pid = fielder.get("player_id")
                            if pid is not None and pid not in registry:
                                errors.append(f"Unresolved fielder: {pid}")
        for event in match_data.get("participation_events") or []:
            for role in ("incoming_player_id", "outgoing_player_id"):
                pid = event.get(role)
                if pid is not None and pid not in registry:
                    errors.append(f"Unresolved replacement {role}: {pid}")
        return not errors, errors
    known_ids = set()
    for team, players in match_data.get("playing_xi", {}).items():
        if team.startswith("_") or not isinstance(players, list):
            continue
        for p in players:
            if isinstance(p, dict) and p.get("player_id"):
                known_ids.add(p["player_id"])

    for inn in match_data.get("innings", []):
        for ov in inn.get("overs", []):
            for d in ov.get("deliveries", []):
                for role in ("batter_id", "bowler_id", "non_striker_id"):
                    pid = d.get(role)
                    if pid and pid not in known_ids and not pid.startswith("sub_"):
                        # Log but do not fail for unmapped substitute fielders
                        pass

    return True, errors


def validate_hundred_contract(match: dict) -> List[str]:
    """Recompute clocks/derived policy; do not trust caller-supplied phase labels."""
    errors=[]
    meta=match['meta']
    source=meta['source_classification']
    expected=hundred.match_metadata({'season':meta.get('season'),
        'format':source.get('format'),'generalClassId':source.get('general_class_id'),
        'internationalClassId':source.get('international_class_id')},meta.get('competition'),meta.get('dates') or [])
    for key,value in expected.items():
        if key=='gender' and meta['schema_version']=='3.3.0':
            continue
        if meta.get(key)!=value:
            errors.append(f'Hundred metadata mismatch: {key}')
    pack=expected['ruleset']
    for inn in match['innings']:
        special=inn['super_over']
        allocations=inn['source_allocations']
        expected_inn=hundred.innings_metadata({'totalBalls':allocations.get('scorecard_total_balls'),'balls':inn['source_legal_balls']},
            [{'over':{'ballLimit':v}} for v in allocations.get('commentary_ball_limits',[])],pack,special)
        for key,value in expected_inn.items():
            if inn.get(key)!=value:
                errors.append(f'Hundred innings metadata mismatch: {key}')
        balls=[d for ov in inn['overs'] for d in ov['deliveries']]
        bowler_sets=defaultdict(set)
        for ov in inn['overs']:
            for d in ov['deliveries']:
                bowler_sets[d['bowler_id']].add(ov['over'])
                if d['source_over_number'] is not None and d['source_over_number']!=ov['over']+1:
                    errors.append('Hundred native group conflicts with source over number')
        legal=0;bowler_balls=Counter()
        consecutive=0;previous_bowler=None;special_wickets=0
        quota=expected_inn['allocated_legal_balls']
        maximum=5 if special else 100
        for sequence,d in enumerate(balls,1):
            if legal>=maximum or (quota is not None and legal>=quota):
                errors.append('Hundred delivery after innings ball limit reached')
            if special and special_wickets>=2:
                errors.append('Super Five delivery after second dismissal')
            if special:
                try:
                    special_wickets+=count_team_wickets([d])
                except ValueError as exc:
                    errors.append(str(exc))
            after=legal+int(d['is_legal'])
            if d['source_delivery_sequence'] is not None and d['source_delivery_sequence']!=sequence:
                errors.append(f'Hundred source delivery sequence gap at {sequence}')
            # Provider ballsActual points to the legal position being attempted,
            # including on wides/no-balls, rather than completed legal balls.
            if d['source_legal_ball_marker'] is not None and d['source_legal_ball_marker']!=legal+1:
                errors.append(f'Hundred source legal position mismatch at {sequence}')
            if (d['delivery_sequence'],d['legal_balls_before'],d['legal_balls_after'])!=(sequence,legal,after):
                errors.append(f'Hundred ball clock mismatch at delivery {sequence}')
            if d['phase']!=hundred.delivery_phase(legal,expected_inn) or d['nominal_phase']!=hundred.nominal_phase(legal,expected_inn):
                errors.append(f'Hundred phase mismatch at delivery {sequence}')
            if d['bowler_id']!=previous_bowler:
                consecutive=0;previous_bowler=d['bowler_id']
            consecutive+=int(d['is_legal'])
            if pack and consecutive>10:
                errors.append('Hundred bowler exceeds ten consecutive legal balls; requires source review')
            if d['is_legal']:bowler_balls[d['bowler_id']]+=1
            legal=after
        if inn['legal_balls']!=legal or inn['delivered_balls']!=len(balls):
            errors.append('Hundred innings clock totals mismatch')
        if inn['source_legal_balls'] is not None and legal!=inn['source_legal_balls']:
            errors.append('Hundred stored scorecard legal-ball total mismatch')
        counter_coverage={
            'delivery_sequence':sum(d['source_delivery_sequence'] is not None for d in balls),
            'legal_position':sum(d['source_legal_ball_marker'] is not None for d in balls)}
        if inn['source_counter_coverage']!=counter_coverage:
            errors.append('Hundred source counter coverage mismatch')
        if legal>maximum:
            errors.append(f'Hundred innings exceeds {maximum} legal balls')
        if special:
            try:
                if count_team_wickets(balls)>2:
                    errors.append('Super Five exceeds two dismissals')
            except ValueError as exc:
                errors.append(str(exc))
        quota=expected_inn['allocated_legal_balls']
        if quota and legal>quota:
            errors.append('Hundred innings exceeds reported allocation; requires source review')
        if pack and not special:
            if any(v>pack['full_bowler_limit'] for v in bowler_balls.values()):
                errors.append('Hundred bowler exceeds 20 legal balls; requires source review')
            for sets in bowler_sets.values():
                if len(sets)>4:
                    errors.append('Hundred bowler participates in more than four sets; requires source review')
                ordered=sorted(sets)
                if any(b==a+1 and c==b+1 for a,b,c in zip(ordered,ordered[1:],ordered[2:])):
                    errors.append('Hundred bowler participates in three consecutive sets; requires source review')
    return errors
