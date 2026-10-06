"""
src/constructor.py
Match JSON v3.0 Constructor from ESPNcricinfo Raw Data.
"""
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

LEAGUE_COMPETITIONS = {
    "Indian Premier League",
    "SA20",
    "The Hundred Men's Competition",
    "International League T20",
    "Major League Cricket"
}

DISMISSAL_KIND_MAP = {
    1: "caught",
    2: "bowled",
    3: "lbw",
    4: "run out",
    5: "stumped",
    6: "hit wicket",
    7: "handled the ball",
    8: "obstructing the field",
    9: "hit the ball twice",
    10: "timed out",
    11: "retired out",
    13: "retired hurt"
}

def determine_format_bucket(raw_format: str, competition_name: Optional[str]) -> Tuple[str, str]:
    """
    Returns (meta_format, db_format_bucket) strictly adhering to GET_FORMAT_BUCKET_SQL:
    - IPL: competition == 'Indian Premier League'
    - T20I: IT20 or (T20 and not a domestic league)
    - T20: domestic T20 leagues
    - ODI: ODI
    - Test: Test
    """
    comp = competition_name or ""
    fmt = (raw_format or "").strip()
    fmt_upper = fmt.upper()

    if comp == "Indian Premier League":
        return "T20", "IPL"
    if comp == "The Hundred Men's Competition" or fmt_upper in ("HUNDRED_BALL", "HUNDRED"):
        return "T20", "T20"
    if fmt_upper in ("IT20", "T20I"):
        return "T20I", "T20I"
    if fmt_upper in ("T20",):
        if comp in LEAGUE_COMPETITIONS:
            return "T20", "T20"
        return "T20I", "T20I"
    if fmt_upper in ("ODI", "ODM"):
        return "ODI", "ODI"
    if fmt_upper in ("TEST", "MDM"):
        return "Test", "Test"

    return fmt, fmt

def construct_match_v3(
    mid: str,
    raw_details: dict,
    commentary_chunks_by_inn: Dict[int, List[dict]],
    player_profiles: dict,
    people_registry: dict,
    venue_map: dict
) -> Tuple[Optional[dict], Optional[str]]:
    """Assemble complete Match JSON v3.0."""
    match_info = raw_details.get("match") or {}
    content = raw_details.get("content") or {}
    series = match_info.get("series") or {}

    comp_name = series.get("name") or (match_info.get("event") or {}).get("name")
    raw_fmt = match_info.get("format")
    meta_fmt, db_fmt_bucket = determine_format_bucket(raw_fmt, comp_name)

    # Dates: Multi-day expansion for Tests
    start_str = (match_info.get("startDate") or "").split("T")[0]
    dates = []
    if start_str:
        if meta_fmt == "Test":
            actual_days = match_info.get("actualDays") or match_info.get("scheduledDays") or 5
            try:
                base_dt = datetime.strptime(start_str, "%Y-%m-%d")
                dates = [(base_dt + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(actual_days)]
            except Exception:
                dates = [start_str]
        else:
            dates = [start_str]

    ground = match_info.get("ground") or {}
    venue_name = ground.get("name") or ground.get("smallName") or "Unknown Venue"
    venue_country = venue_map.get(venue_name) or (match_info.get("country") or {}).get("name")
    city = ground.get("town", {}).get("name") if isinstance(ground.get("town"), dict) else ground.get("town")

    # Build internal player map
    player_map = {}
    team_name_by_id = {}
    for t in (match_info.get("teams") or []):
        tm = t.get("team") or {}
        if tm.get("id"):
            team_name_by_id[tm["id"]] = tm.get("name")

    for inn in (content.get("innings") or []):
        for b in (inn.get("inningBatsmen") or []):
            pl = b.get("player") or {}
            if pl.get("id"):
                player_map[pl["id"]] = {
                    "objectId": pl.get("objectId"),
                    "name": pl.get("name"),
                    "longName": pl.get("longName"),
                    "teamId": (inn.get("team") or {}).get("id")
                }
        for bw in (inn.get("inningBowlers") or []):
            pl = bw.get("player") or {}
            if pl.get("id"):
                player_map[pl["id"]] = {
                    "objectId": pl.get("objectId"),
                    "name": pl.get("name"),
                    "longName": pl.get("longName"),
                    "teamId": (inn.get("team") or {}).get("id")
                }

    for tp in (content.get("matchPlayers", {}).get("teamPlayers") or []):
        for p in (tp.get("players") or []):
            pl = p.get("player") or {}
            if pl.get("id"):
                player_map[pl["id"]] = {
                    "objectId": pl.get("objectId"),
                    "name": pl.get("name"),
                    "longName": pl.get("longName")
                }

    # Dismissal fielders map
    dismissal_fielders_map = {}
    for inn_idx, inn in enumerate(content.get("innings") or []):
        inn_num = inn.get("inningNumber") or (inn_idx + 1)
        for b in (inn.get("inningBatsmen") or []):
            b_id = (b.get("player") or {}).get("id")
            fielders = []
            if b.get("dismissalFielders"):
                for f in (b.get("dismissalFielders") or []):
                    if not f or not isinstance(f, dict): continue
                    fp = f.get("player")
                    if fp and isinstance(fp, dict):
                        f_name = fp.get("name") or fp.get("longName")
                        f_obj = fp.get("objectId")
                        f_prof = player_profiles.get(f"espn_{f_obj}") or {}
                        fielders.append({
                            "name": f_name,
                            "player_id": f"espn_{f_obj}" if f_obj else None,
                            "cricsheet_id": f_prof.get("cricsheet_id")
                        })
            if b_id and fielders:
                dismissal_fielders_map[(inn_num, b_id)] = fielders

    # Playing XI with canonical espn_<id> primary keys
    teams = [(t.get("team") or {}).get("name") for t in (match_info.get("teams") or []) if t.get("team")]
    playing_xi = {}

    for inn in (content.get("innings") or []):
        t_name = (inn.get("team") or {}).get("name")
        if not t_name or t_name in playing_xi:
            continue

        team_players = []
        seen_pids = set()

        # True batting order
        for b in (inn.get("inningBatsmen") or []):
            pl = b.get("player") or {}
            obj_id = pl.get("objectId")
            if not obj_id or obj_id in seen_pids: continue
            seen_pids.add(obj_id)
            prof = player_profiles.get(f"espn_{obj_id}") or {}

            team_players.append({
                "player_id": f"espn_{obj_id}",
                "cricsheet_id": prof.get("cricsheet_id"),
                "espn_id": obj_id,
                "name": prof.get("name") or pl.get("name"),
                "batting_hand": prof.get("batting_hand"),
                "bowling_arm": prof.get("bowling_arm"),
                "bowling_style": prof.get("bowling_style"),
                "bowling_type": prof.get("bowling_type"),
                "role": prof.get("role"),
                "batting_position": len(team_players) + 1
            })

        for p in (inn.get("inningDidNotBats") or []):
            pl = p.get("player") or {}
            obj_id = pl.get("objectId")
            if not obj_id or obj_id in seen_pids: continue
            seen_pids.add(obj_id)
            prof = player_profiles.get(f"espn_{obj_id}") or {}

            team_players.append({
                "player_id": f"espn_{obj_id}",
                "cricsheet_id": prof.get("cricsheet_id"),
                "espn_id": obj_id,
                "name": prof.get("name") or pl.get("name"),
                "batting_hand": prof.get("batting_hand"),
                "bowling_arm": prof.get("bowling_arm"),
                "bowling_style": prof.get("bowling_style"),
                "bowling_type": prof.get("bowling_type"),
                "role": prof.get("role"),
                "batting_position": len(team_players) + 1
            })

        playing_xi[t_name] = team_players[:11]

    # Process Innings & Deliveries
    enriched_innings = []
    total_balls_count = 0
    total_tracked_balls = 0

    super_over_counter = 3

    for inn_idx, inn in enumerate(content.get("innings") or []):
        is_so = bool(inn.get("isSuperOver"))
        if is_so and db_fmt_bucket in ("T20", "T20I", "IPL", "ODI"):
            inn_num = super_over_counter
            super_over_counter += 1
        else:
            inn_num = inn.get("inningNumber") or (inn_idx + 1)

        batting_team = (inn.get("team") or {}).get("name")
        bowling_team = teams[1] if (len(teams) > 1 and batting_team == teams[0]) else (teams[0] if teams else None)

        raw_balls = commentary_chunks_by_inn.get(inn_idx + 1) or []
        # Deduplicate balls by id if present, preserving first occurrence
        seen_ball_ids = set()
        deduped_balls = []
        for b in raw_balls:
            b_id = b.get("id")
            if b_id:
                if b_id in seen_ball_ids:
                    continue
                seen_ball_ids.add(b_id)
            deduped_balls.append(b)
        raw_balls = deduped_balls

        # Sort ascending chronologically by oversUnique, ballNumber, or oversActual
        raw_balls.sort(key=lambda b: (
            b.get("oversUnique") if b.get("oversUnique") is not None else (
                b.get("ballNumber") if b.get("ballNumber") is not None else (
                    b.get("oversActual") or 0
                )
            )
        ))

        overs_dict = {}
        for b in raw_balls:
            if b.get("oversActual") is None:
                continue
            ov_num = (b.get("overNumber", 1) - 1) if b.get("overNumber") else int(float(b.get("oversActual", 0)))
            overs_dict.setdefault(ov_num, []).append(b)


        enriched_overs = []
        seen_dismissed_batters = set()
        prev_inning_cricinfo_total = 0

        for ov_num in sorted(overs_dict.keys()):
            ov_balls = overs_dict[ov_num]

            if db_fmt_bucket in ("T20", "T20I", "IPL"):
                phase = "powerplay" if ov_num <= 5 else ("middle" if ov_num <= 14 else "death")
            elif db_fmt_bucket == "ODI":
                phase = "powerplay" if ov_num <= 9 else ("middle" if ov_num <= 39 else "death")
            else:
                phase = "neutral"

            enriched_deliveries = []
            for b_idx, ball in enumerate(ov_balls, 1):
                total_balls_count += 1
                b_int_id = ball.get("batsmanPlayerId")
                b_info = player_map.get(b_int_id) or {}
                b_obj_id = b_info.get("objectId")
                b_pid = f"espn_{b_obj_id}" if b_obj_id else f"espn_{b_int_id}"

                bw_int_id = ball.get("bowlerPlayerId")
                bw_info = player_map.get(bw_int_id) or {}
                bw_obj_id = bw_info.get("objectId")
                bw_pid = f"espn_{bw_obj_id}" if bw_obj_id else f"espn_{bw_int_id}"

                ns_int_id = ball.get("nonStrikerPlayerId")
                ns_info = player_map.get(ns_int_id) or {}
                ns_obj_id = ns_info.get("objectId")
                ns_pid = f"espn_{ns_obj_id}" if ns_obj_id else (f"espn_{ns_int_id}" if ns_int_id else None)

                bat_runs = ball.get("batsmanRuns") or 0
                byes = ball.get("byes") or 0
                legbyes = ball.get("legbyes") or 0
                wides = ball.get("wides") or 0
                noballs = ball.get("noballs") or 0
                penalties = ball.get("penalties") or 0
                extras_total = byes + legbyes + wides + noballs + penalties
                tot_runs = ball.get("totalRuns") if ball.get("totalRuns") is not None else (bat_runs + extras_total)

                # Detect unallocated +5 penalty runs (MCC Law 28.3, e.g. ball hit helmet)
                c_tot = ball.get("totalInningRuns")
                if c_tot is not None and prev_inning_cricinfo_total > 0:
                    step = c_tot - prev_inning_cricinfo_total
                    if step == (tot_runs + 5):
                        penalties += 5
                        extras_total += 5
                        tot_runs += 5

                if c_tot is not None:
                    prev_inning_cricinfo_total = c_tot

                is_legal = (wides == 0 and noballs == 0)

                extras_dict = {}
                if wides: extras_dict["wides"] = wides
                if noballs: extras_dict["noballs"] = noballs
                if byes: extras_dict["byes"] = byes
                if legbyes: extras_dict["legbyes"] = legbyes
                if penalties: extras_dict["penalty"] = penalties

                # Wickets
                wickets_list = []
                if ball.get("isWicket"):
                    out_int_id = ball.get("outPlayerId") or b_int_id
                    dtype = ball.get("dismissalType")
                    kind = DISMISSAL_KIND_MAP.get(dtype, "unknown")

                    # Deduplicate accidental duplicate wicket entries for the same batter in an innings
                    if out_int_id and out_int_id in seen_dismissed_batters and kind != "retired hurt":
                        pass
                    else:
                        if out_int_id and kind != "retired hurt":
                            seen_dismissed_batters.add(out_int_id)
                        out_info = player_map.get(out_int_id) or {}
                        out_obj_id = out_info.get("objectId")
                        out_pid = f"espn_{out_obj_id}" if out_obj_id else f"espn_{out_int_id}"
                        out_name = out_info.get("name") or b_info.get("name")

                        fielders = dismissal_fielders_map.get((inn_idx + 1, out_int_id), [])

                        if kind == "caught" and fielders:
                            if fielders[0].get("player_id") == bw_pid:
                                kind = "caught and bowled"

                        wickets_list.append({
                            "player_out_id": out_pid,
                            "player_out_name": out_name,
                            "kind": kind,
                            "fielders": fielders
                        })

                # Tactical traits (Clean sentinels: null over fake zeros)
                wx = ball.get("wagonX")
                wy = ball.get("wagonY")
                wzone = ball.get("wagonZone")
                has_wagon = (wx is not None and wy is not None and wx > 0 and wy > 0)
                if has_wagon:
                    total_tracked_balls += 1

                tactical = {
                    "wagon_coords": [wx, wy] if has_wagon else None,
                    "wagon_zone": wzone if (wzone is not None and wzone > 0) else None,
                    "pitch_line": ball.get("pitchLine"),
                    "pitch_length": ball.get("pitchLength"),
                    "shot_type": ball.get("shotType"),
                    "shot_control": ball.get("shotControl")
                }

                # DRS Review from events
                review_obj = None
                for ev in (ball.get("events") or []):
                    if ev.get("type") == "DRS_REVIEW":
                        rev_team_id = ev.get("teamId")
                        rev_team = team_name_by_id.get(rev_team_id)
                        review_obj = {
                            "by": rev_team,
                            "batter": b_info.get("name"),
                            "bowler": bw_info.get("name"),
                            "decision": "upheld" if ev.get("isSuccessful") else "struck down",
                            "type": "drs"
                        }

                deliv_obj = {
                    "ball_number": b_idx,
                    "actual_delivery": str(ball.get("oversActual")),
                    "is_legal": is_legal,
                    "batter_id": b_pid,
                    "bowler_id": bw_pid,
                    "non_striker_id": ns_pid,
                    "runs": {
                        "batter": bat_runs,
                        "extras": extras_total,
                        "total": tot_runs
                    },
                    "extras": extras_dict,
                    "wickets": wickets_list,
                    "total_inning_runs": ball.get("totalInningRuns"),
                    "phase": phase,
                    "tactical": tactical,
                    "review": review_obj
                }
                enriched_deliveries.append(deliv_obj)

            enriched_overs.append({
                "over": ov_num,
                "deliveries": enriched_deliveries
            })

        enriched_innings.append({
            "innings_number": inn_num,
            "batting_team": batting_team,
            "bowling_team": bowling_team,
            "target": inn.get("target"),
            "declared": bool(inn.get("isDeclared")),
            "follow_on": bool(inn.get("isFollowOn")),
            "super_over": is_so,
            "overs": enriched_overs
        })

    # Toss & Outcome
    toss_winner_team = match_info.get("tossWinnerTeamId")
    toss_winner_name = team_name_by_id.get(toss_winner_team)
    toss_choice = match_info.get("tossWinnerChoice")
    toss_decision = "field" if toss_choice == 2 else ("bat" if toss_choice == 1 else None)

    winner_team_id = match_info.get("winnerTeamId")
    winner_name = team_name_by_id.get(winner_team_id)
    status_text = match_info.get("statusText") or ""

    win_by_runs = None
    win_by_wkts = None
    win_by_innings = None
    result = None
    eliminator = None
    method = None

    if "dls" in status_text.lower() or "d/l" in status_text.lower():
        method = "DLS"

    m_inns = re.search(r'won by an innings and (\d+) run', status_text, re.I)
    if m_inns:
        win_by_innings = 1
        win_by_runs = int(m_inns.group(1))
    else:
        m_runs = re.search(r'won by (\d+) run', status_text, re.I)
        if m_runs: win_by_runs = int(m_runs.group(1))
        m_wkts = re.search(r'won by (\d+) wicket', status_text, re.I)
        if m_wkts: win_by_wkts = int(m_wkts.group(1))

    if "tied" in status_text.lower():
        result = "tie"
        if winner_name:
            eliminator = winner_name
    elif "no result" in status_text.lower() or "abandoned" in status_text.lower():
        result = "no result"
        winner_name = None
    elif "drawn" in status_text.lower():
        result = "draw"
        winner_name = None

    # Officials in DB format
    umpires = [u.get("player", {}).get("name") for u in (match_info.get("umpires") or []) if u and u.get("player", {}).get("name")]
    tv_umpire = next((u.get("player", {}).get("name") for u in (match_info.get("tvUmpires") or []) if u and u.get("player", {}).get("name")), None)
    match_referee = next((u.get("player", {}).get("name") for u in (match_info.get("matchReferees") or []) if u and u.get("player", {}).get("name")), None)

    db_playing_xi = dict(playing_xi)
    db_playing_xi["_umpires"] = umpires
    db_playing_xi["_referee"] = match_referee

    tactical_coverage = round(total_tracked_balls / total_balls_count, 3) if total_balls_count > 0 else 0.0

    raw_bpo = match_info.get("ballsPerOver")
    if raw_bpo and isinstance(raw_bpo, int) and raw_bpo > 0:
        balls_per_over = raw_bpo
    elif comp_name == "The Hundred Men's Competition":
        balls_per_over = 5
    else:
        balls_per_over = 6

    match_v3 = {
        "match_id": str(mid),
        "meta": {
            "schema_version": "3.0.0",
            "format": meta_fmt,
            "format_bucket": db_fmt_bucket,
            "competition": comp_name,
            "season": match_info.get("season"),
            "dates": dates,
            "venue": venue_name,
            "venue_country": venue_country,
            "city": city,
            "gender": "male",
            "day_night": "day/night" if match_info.get("dayNight") else "day",
            "match_stage": (match_info.get("event") or {}).get("stage"),
            "match_number": (match_info.get("event") or {}).get("matchNumber"),
            "match_group": (match_info.get("event") or {}).get("group"),
            "balls_per_over": balls_per_over,
            "tactical_coverage": tactical_coverage
        },
        "outcome": {
            "winner": winner_name,
            "win_by_runs": win_by_runs,
            "win_by_wickets": win_by_wkts,
            "win_by_innings": win_by_innings,
            "result": result,
            "eliminator": eliminator,
            "method": method
        },
        "toss": {
            "winner": toss_winner_name,
            "decision": toss_decision
        },
        "playing_xi": db_playing_xi,
        "innings": enriched_innings
    }

    return match_v3, None
