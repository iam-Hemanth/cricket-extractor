"""Lossless source-native envelopes. This module has no ingestion side effects."""
import copy
import re

from ingestion.source_attribution import apply_source_attribution


def _quantity(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f'Invalid nonnegative quantity: {label}')
    return value


def normalize_match(source: dict, source_sha256: str, identity_index: dict) -> dict:
    for name, kind in (('meta', dict), ('players', dict), ('playing_xi', dict), ('participation_events', list), ('innings', list)):
        if not isinstance(source.get(name), kind):
            raise ValueError(f'Invalid source shape: {name}')
    statuses = source['meta'].get('playing_xi_status', {})
    if not isinstance(statuses, dict) or any(not isinstance(v, str) for v in statuses.values()):
        raise ValueError('Invalid playing_xi_status shape')
    if not re.fullmatch(r'[a-f0-9]{64}', source_sha256):
        raise ValueError('Invalid source SHA256')
    if source['meta']['schema_version'] != '3.3.0':
        raise ValueError('Unsupported source contract')
    players = source['players']
    for pid, player in players.items():
        if pid != player['player_id'] or pid not in identity_index['players']:
            raise ValueError('Participant identity missing from verified index')
        if player['espn_id'] != identity_index['players'][pid]['espn_id']:
            raise ValueError('Participant provider identity conflict')
        cid = player.get('cricsheet_id')
        if cid and identity_index['aliases'].get(cid) != pid:
            raise ValueError('Participant alias missing from verified index')

    def actor(pid, nullable=False):
        if pid is None and nullable:
            return
        if not isinstance(pid, str) or pid not in players:
            raise ValueError(f'Unresolved participant: {pid!r}')

    def reference(node):
        if isinstance(node, dict):
            if 'player_id' in node:
                pid = node['player_id']
                actor(pid, nullable=True)
                if pid is not None and 'espn_id' in node:
                    if type(node['espn_id']) is not int or node['espn_id'] != players[pid]['espn_id']:
                        raise ValueError('Embedded provider identity contradicts participant')
                cid = node.get('cricsheet_id')
                if cid and identity_index['aliases'].get(cid) != pid:
                    raise ValueError('Embedded alias contradicts participant')
            for key, value in node.items():
                if key.endswith('_player_id'):
                    actor(value, nullable=True)
                reference(value)
        elif isinstance(node, list):
            for value in node:
                reference(value)

    for team, lineup in source['playing_xi'].items():
        if not team.startswith('_'):
            if not isinstance(lineup, list):
                raise ValueError('Invalid lineup shape')
            for entry in lineup:
                actor(entry.get('player_id') if isinstance(entry, dict) else entry)
                reference(entry)
    reference(source['participation_events'])
    reference(list(players.values()))

    def annotate(record, key, value):
        if key in record and (type(record[key]) is not type(value) or record[key] != value):
            raise ValueError(f'Conflicting source-owned annotation: {key}')
        record[key] = value

    result = copy.deepcopy(source)
    annotate(result, 'source_sha256', source_sha256)
    annotate(result, 'player_aliases', {
        cid: pid for cid, pid in identity_index['aliases'].items() if pid in players
    })
    for innings in result['innings']:
        super_over = innings['super_over']
        if type(super_over) is not bool:
            raise ValueError('Invalid explicit decider flag')
        standard = innings.get('include_in_standard_stats', not super_over)
        if type(standard) is not bool:
            raise ValueError('Conflicting standard-stat eligibility')
        innings['include_in_standard_stats'] = standard
        ordinal = 0
        recorded_timed_out = 0
        for over in innings['overs']:
            for delivery in over['deliveries']:
                ordinal += 1
                actor(delivery['batter_id'])
                actor(delivery['bowler_id'])
                actor(delivery['non_striker_id'], nullable=True)
                extras = delivery['extras']
                for name, value in extras.items():
                    _quantity(value, f'extras.{name}')
                runs = delivery['runs']
                for name in ('batter', 'extras', 'total'):
                    _quantity(runs[name], f'runs.{name}')
                legal = not (extras.get('wides', 0) or extras.get('noballs', 0))
                if type(delivery['is_legal']) is not bool or delivery['is_legal'] != legal:
                    raise ValueError('Source legal flag contradicts extras')
                if runs['extras'] != sum(extras.values()) or runs['total'] != runs['batter'] + runs['extras']:
                    raise ValueError('Source run components contradict total')
                annotate(delivery, 'delivery_ordinal', ordinal)
                annotate(delivery, 'bowler_runs', runs['batter'] + extras.get('wides', 0) + extras.get('noballs', 0))
                for wicket in delivery['wickets']:
                    actor(wicket['player_out_id'])
                    recorded_timed_out += wicket['kind'] == 'timed out'
                    reference(wicket)
        reported = innings['source_scorecard'].get('timed_out')
        if reported is None:
            annotate(innings, 'source_only_timed_out_count', None)
        else:
            _quantity(reported, 'scorecard.timed_out')
            if reported < recorded_timed_out:
                raise ValueError('Timed-out events exceed source scorecard count')
            annotate(innings, 'source_only_timed_out_count', reported - recorded_timed_out)
    return apply_source_attribution(result)
