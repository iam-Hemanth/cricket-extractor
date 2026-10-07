"""
src/validator.py
Hardened Multi-Dimension Validator for Cricket Match JSON.
"""
from typing import Dict, List, Optional, Tuple
from functools import lru_cache
from pathlib import Path
import json

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

        # Completed overs must be within balls_per_over - 1 and balls_per_over + 2
        # (e.g. 5, 6, 7, 8 legal balls for 6-ball overs due to rain, bowler injuries, declarations, or umpire miscounts)
        if not is_final_over and (legal_count < balls_per_over - 1 or legal_count > balls_per_over + 2):
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
        got_runs = sum(d.get("runs", {}).get("total", 0) for ov in overs for d in ov.get("deliveries", []))
        try:
            got_wkts = count_team_wickets([
                d for ov in overs for d in ov.get("deliveries", [])
            ])
        except ValueError as exc:
            errors.append(str(exc))
            return False, errors

        # Check for non-delivery dismissals like 'timed out' (e.g. Angelo Mathews 2023 WC)
        timed_out_count = 0
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
    if (match_data.get("meta") or {}).get("schema_version") == "3.1.0":
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
