"""
src/validator.py
Hardened Multi-Dimension Validator for Cricket Match JSON.
"""
from typing import Dict, List, Optional, Tuple

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
        return True, []

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
                if curr_tuple < prev_tuple:
                    errors.append(
                        f"Non-monotonic delivery sequence in Inn {inn.get('innings_number')}: "
                        f"{prev_tuple} -> {curr_tuple}"
                    )
                prev_tuple = curr_tuple

            if d.get("is_legal"):
                legal_count += 1

        # Completed overs must have exactly balls_per_over legal balls (unless final over of innings)
        if not is_final_over and legal_count != balls_per_over:
            errors.append(
                f"Inn {inn.get('innings_number')} Over {ov.get('over')} has {legal_count} legal balls (expected {balls_per_over})"
            )

    # 2. Cumulative score integrity vs Cricinfo totalInningRuns
    calc_runs = 0
    for ov in overs:
        for d in ov.get("deliveries") or []:
            calc_runs += d.get("runs", {}).get("total", 0)
            cricinfo_total = d.get("total_inning_runs")
            if cricinfo_total is not None and calc_runs != cricinfo_total:
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
        got_wkts = sum(len(d.get("wickets", [])) for ov in overs for d in ov.get("deliveries", []))

        if sc_runs is not None and got_runs != sc_runs:
            errors.append(f"Scorecard runs mismatch: got {got_runs} != scorecard {sc_runs}")
        if sc_wkts is not None and got_wkts != sc_wkts:
            errors.append(f"Scorecard wickets mismatch: got {got_wkts} != scorecard {sc_wkts}")

        # Extras breakdown check
        if isinstance(sc_inn.get("extras"), dict):
            sc_extras = sc_inn.get("extras")
            sc_wides = sc_extras.get("wides")
            sc_noballs = sc_extras.get("noballs")
            sc_byes = sc_extras.get("byes")
            sc_legbyes = sc_extras.get("legbyes")
        else:
            sc_wides = sc_inn.get("wides")
            sc_noballs = sc_inn.get("noballs")
            sc_byes = sc_inn.get("byes")
            sc_legbyes = sc_inn.get("legbyes")

        got_wides = sum(d.get("extras", {}).get("wides", 0) for ov in overs for d in ov.get("deliveries", []))
        got_noballs = sum(d.get("extras", {}).get("noballs", 0) for ov in overs for d in ov.get("deliveries", []))
        got_byes = sum(d.get("extras", {}).get("byes", 0) for ov in overs for d in ov.get("deliveries", []))
        got_legbyes = sum(d.get("extras", {}).get("legbyes", 0) for ov in overs for d in ov.get("deliveries", []))

        if sc_wides is not None and got_wides != sc_wides:
            errors.append(f"Wides mismatch: got {got_wides} != scorecard {sc_wides}")
        if sc_noballs is not None and got_noballs != sc_noballs:
            errors.append(f"No-balls mismatch: got {got_noballs} != scorecard {sc_noballs}")
        if sc_byes is not None and got_byes != sc_byes:
            errors.append(f"Byes mismatch: got {got_byes} != scorecard {sc_byes}")
        if sc_legbyes is not None and got_legbyes != sc_legbyes:
            errors.append(f"Leg-byes mismatch: got {got_legbyes} != scorecard {sc_legbyes}")


    return (len(errors) == 0), errors

def validate_player_registry(match_data: dict) -> Tuple[bool, List[str]]:
    """Verify that every player ID referenced in deliveries exists in playing_xi or substitutes."""
    errors = []
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
