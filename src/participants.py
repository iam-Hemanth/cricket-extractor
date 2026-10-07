"""Explicit source identities and participation history, independent of XI size."""

EVENT_TYPES = {
    "IMPACT_PLAYER": "impact_player", "CONCUSSION_REPLACEMENT": "concussion_replacement",
    "SUBSTITUTE_FIELDER": "substitute_fielder", "PRE_MATCH_REPLACEMENT": "pre_match_replacement",
    "SUPER_SUB": "historical_supersub", "HISTORICAL_SUPERSUB": "historical_supersub",
    "OTHER_AUTHORIZED_REPLACEMENT": "other_authorized_replacement", "REPLACEMENT": "unknown",
}


def _id(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _event_sources(details, commentary):
    for index, event in enumerate((details.get("content") or {}).get("participationEvents") or []):
        yield event, None, f"/content/participationEvents/{index}"
    for inn, balls in commentary.items():
        for index, ball in enumerate(balls):
            for event_index, event in enumerate(ball.get("events") or []):
                if isinstance(event, dict):
                    yield event, ball, f"/commentary/{inn}/{index}/events/{event_index}"


def _player_sources(details, commentary):
    content = details.get("content") or {}
    for group_index, group in enumerate((content.get("matchPlayers") or {}).get("teamPlayers") or []):
        team_id = _id((group.get("team") or {}).get("id"))
        for index, entry in enumerate(group.get("players") or []):
            yield entry.get("player") or {}, team_id, "listed", f"/content/matchPlayers/teamPlayers/{group_index}/players/{index}/player"
    for inn_index, inn in enumerate(content.get("innings") or []):
        for collection, role in (("inningBatsmen", "listed"), ("inningDidNotBats", "listed"), ("inningBowlers", "bowler")):
            team_id = _id((inn.get("team") or {}).get("id")) if collection != "inningBowlers" else None
            for index, entry in enumerate(inn.get(collection) or []):
                ref = f"/content/innings/{inn_index}/{collection}/{index}"
                yield entry.get("player") or {}, team_id, role, ref + "/player"
                for f_index, fielder in enumerate(entry.get("dismissalFielders") or []):
                    if isinstance(fielder, dict):
                        yield fielder.get("player") or {}, None, "fielder", ref + f"/dismissalFielders/{f_index}/player"
    for inn, balls in commentary.items():
        for index, ball in enumerate(balls):
            over = ball.get("over") or {}
            for collection, role in (("overEndBatsmen", "listed"), ("overEndBowlers", "bowler")):
                for p_index, entry in enumerate(over.get(collection) or []):
                    yield entry.get("player") or {}, None, role, f"/commentary/{inn}/{index}/over/{collection}/{p_index}/player"
    for event, ball, ref in _event_sources(details, commentary):
        for key in ("incomingPlayer", "outgoingPlayer", "replacingPlayer", "player", "batsman", "bowler", "fielder"):
            value = event.get(key)
            if isinstance(value, dict):
                value = value.get("player") if isinstance(value.get("player"), dict) else value
                yield value, _id(event.get("teamId")), "fielder" if key == "fielder" else "listed", ref + "/" + key
        for index, entry in enumerate(event.get("fielders") or []):
            if isinstance(entry, dict):
                yield entry.get("player") or entry, _id(event.get("teamId")), "fielder", ref + f"/fielders/{index}"


def build_participant_registry(raw_details, commentary_chunks_by_inn, player_profiles, people_registry):
    id_map, players, issues = {}, {}, []
    for source, team_id, role, ref in _player_sources(raw_details, commentary_chunks_by_inn):
        internal, public = _id(source.get("id")), _id(source.get("objectId"))
        if public is None:
            continue
        pid = f"espn_{public}"
        if internal is not None:
            previous = id_map.get(internal)
            if previous is not None and previous != pid:
                issues.append({"reason": "conflicting_object_id", "source_internal_id": internal,
                               "source_refs": [ref], "player_ids": [previous, pid]})
            else:
                id_map[internal] = pid
        profile = player_profiles.get(pid) or {}
        profile_cid, registry_cid = profile.get("cricsheet_id"), people_registry.get(str(public))
        if profile_cid and registry_cid and profile_cid != registry_cid:
            issues.append({"reason": "conflicting_cricsheet_id", "player_id": pid, "source_refs": [ref]})
        cid = registry_cid or profile_cid
        if pid not in players:
            players[pid] = {"player_id": pid, "espn_id": public, "source_internal_ids": [],
                            "name": profile.get("name") or source.get("name") or source.get("longName"),
                            "cricsheet_id": cid, "team_ids": [], "participant_roles": [], "source_refs": []}
            for trait in ("batting_hand", "bowling_arm", "bowling_style", "bowling_type", "role"):
                players[pid][trait] = profile.get(trait)
        record = players[pid]
        changed = not record["source_refs"]
        if cid and record["cricsheet_id"] and cid != record["cricsheet_id"]:
            issues.append({"reason": "conflicting_cricsheet_id", "player_id": pid, "source_refs": [ref]})
        elif cid:
            record["cricsheet_id"] = cid
        for value, key in ((internal, "source_internal_ids"), (team_id, "team_ids"), (role, "participant_roles")):
            if value is not None and value not in record[key]:
                record[key].append(value)
                changed = True
        if changed:
            record["source_refs"].append(ref)
    # Actual commentary actions are separate from merely being listed on a roster.
    for inn, balls in commentary_chunks_by_inn.items():
        for index, ball in enumerate(balls):
            for key, role in (("batsmanPlayerId", "batter"), ("nonStrikerPlayerId", "batter"), ("bowlerPlayerId", "bowler")):
                pid = id_map.get(_id(ball.get(key)))
                if pid in players and role not in players[pid]["participant_roles"]:
                    players[pid]["participant_roles"].append(role)
                    players[pid]["source_refs"].append(f"/commentary/{inn}/{index}/{key}")
    return id_map, players, issues


def extract_participation_events(raw_details, commentary_chunks_by_inn, id_map, players):
    results, seen = [], set()
    for source, ball, ref in _event_sources(raw_details, commentary_chunks_by_inn):
        kind = EVENT_TYPES.get(str(source.get("type") or "").upper())
        upstream_replacement = source.get("type") == "PLAYER_REPLACEMENT"
        if upstream_replacement:
            # Code 8 is evidenced in archived IPL 2023 impact-player events.
            competition = (((raw_details.get("match") or {}).get("series") or {}).get("name"))
            kind = "impact_player" if source.get("playerReplacementType") == 8 and competition == "Indian Premier League" else "unknown"
        if kind is None:
            continue
        identities = {}
        for direction in ("incoming", "outgoing"):
            value = source.get(direction + "Player")
            if upstream_replacement:
                value = source.get("player" if direction == "incoming" else "replacingPlayer")
            if isinstance(value, dict):
                value = value.get("player") if isinstance(value.get("player"), dict) else value
                internal, public = _id(value.get("id")), _id(value.get("objectId"))
                pid = id_map.get(internal) if internal is not None else (f"espn_{public}" if public else None)
            else:
                internal = _id(source.get(direction + "PlayerId"))
                pid = id_map.get(internal)
            if pid is not None and pid not in players:
                raise ValueError(f"Unknown {direction} replacement player at {ref}")
            if internal is not None and pid is None:
                raise ValueError(f"Unresolved {direction} replacement source ID {internal} at {ref}")
            identities[direction + "_player_id"] = pid
        event = {"type": kind, "team_id": _id(source.get("teamId")), **identities,
                 "innings_number": source.get("inningNumber"),
                 "source_ball_id": source.get("sourceBallId"),
                 "restrictions": source.get("restrictions") if isinstance(source.get("restrictions"), dict) else None,
                 "source_refs": [ref]}
        # A ball carrying an explicit event is evidence of timing; first performance alone is not.
        if ball is not None:
            event["innings_number"] = event["innings_number"] or ball.get("inningNumber")
            event["source_ball_id"] = event["source_ball_id"] or ball.get("id")
        key = (kind, event["team_id"], identities["incoming_player_id"], identities["outgoing_player_id"],
               event["innings_number"], event["source_ball_id"])
        if key not in seen:
            results.append(event)
            seen.add(key)
    return results


def confirmed_lineups(raw_details, players, participation_events=None):
    teams = [(entry.get("team") or {}).get("name") for entry in (raw_details.get("match") or {}).get("teams") or []]
    lineups = {team: [] for team in teams if team}
    status = {team: "unconfirmed" for team in lineups}
    for group in (((raw_details.get("content") or {}).get("matchPlayers") or {}).get("teamPlayers") or []):
        if group.get("type") != "PLAYING":
            continue
        team = (group.get("team") or {}).get("name")
        entries = [entry for entry in group.get("players") or []
                   if not entry.get("isImpactPlayer") and not entry.get("isConcussionReplacement")]
        pids = [f"espn_{_id((entry.get('player') or {}).get('objectId'))}" for entry in entries]
        source_pids = set(pids)
        ambiguous = False
        # Reconstruct an original XI only from an explicit incoming/outgoing relation.
        for event in participation_events or []:
            incoming, outgoing = event.get("incoming_player_id"), event.get("outgoing_player_id")
            if (event["type"] == "unknown" and outgoing is not None and incoming in source_pids
                    and event.get("team_id") == _id((group.get("team") or {}).get("id"))):
                ambiguous = True
            if (event["type"] in {"impact_player", "concussion_replacement", "historical_supersub", "other_authorized_replacement"}
                    and event.get("team_id") == _id((group.get("team") or {}).get("id"))
                    and incoming in pids and outgoing in pids):
                pids.remove(incoming)
            elif (event["type"] in {"impact_player", "concussion_replacement", "historical_supersub", "other_authorized_replacement"}
                  and event.get("team_id") == _id((group.get("team") or {}).get("id"))
                  and incoming in source_pids and outgoing not in source_pids):
                ambiguous = True
        if not ambiguous and team and len(pids) == 11 and len(set(pids)) == 11 and all(pid in players for pid in pids):
            lineups[team] = [dict(players[pid]) for pid in pids]
            status[team] = "confirmed"
    return lineups, status
