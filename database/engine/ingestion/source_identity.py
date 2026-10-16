"""Explicit provider identities and byte provenance, without DB or network access."""
import copy
import hashlib
import json
import re


def verify_source_bytes(raw_bytes: bytes, expected_mid: str, expected_sha256: str) -> dict:
    if not re.fullmatch(r'[0-9]+', expected_mid):
        raise ValueError('Invalid expected match ID')
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('Invalid expected SHA256')
    if hashlib.sha256(raw_bytes).hexdigest() != expected_sha256:
        raise ValueError('Source SHA256 mismatch')
    source = json.loads(raw_bytes)
    if not isinstance(source, dict) or source.get('match_id') != expected_mid:
        raise ValueError('Source match ID mismatch')
    if (source.get('meta') or {}).get('schema_version') != '3.3.0':
        raise ValueError('Only admitted schema 3.3.0 is supported')
    return source


def build_identity_index(registry_rows: list[dict], source_players: list[dict]) -> dict:
    registry = {}
    for row in registry_rows:
        cid = row.get('identifier')
        if not isinstance(cid, str) or not cid:
            raise ValueError('Missing registry identifier')
        keys = {str(row[k]) for k in ('key_cricinfo', 'key_cricinfo_2', 'key_cricinfo_3') if row.get(k)}
        if cid in registry and registry[cid] != keys:
            raise ValueError('Conflicting registry rows')
        registry[cid] = keys
    players, aliases = {}, {}
    for player in source_players:
        public = player.get('espn_id')
        pid = player.get('player_id')
        if type(public) is not int or public <= 0 or pid != f'espn_{public}':
            raise ValueError('Invalid canonical ESPN identity')
        cid = player.get('cricsheet_id')
        if cid is not None and (not isinstance(cid, str) or not cid):
            raise ValueError('Invalid asserted Cricsheet alias')
        if cid:
            if str(public) not in registry.get(cid, set()):
                raise ValueError('Asserted crosswalk lacks matching provider key')
            if cid in aliases and aliases[cid] != pid:
                raise ValueError('Cricsheet alias collision')
            aliases[cid] = pid
        old = players.get(pid)
        if old and old.get('cricsheet_id') and cid and old['cricsheet_id'] != cid:
            raise ValueError('Conflicting crosswalk for canonical player')
        if old is None or (cid and not old.get('cricsheet_id')):
            players[pid] = copy.deepcopy(player)
    return {'players': players, 'aliases': aliases}
