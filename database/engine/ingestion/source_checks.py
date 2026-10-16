"""Independent per-innings counters; never infer quantities from rounded rates."""
from collections import defaultdict

from ingestion.source_attribution import apply_source_attribution

NON_DISMISSALS = {'retired hurt', 'retired not out'}
BOWLER_DISMISSALS = {'caught', 'caught and bowled', 'bowled', 'lbw', 'stumped', 'hit wicket'}
OTHER_DISMISSALS = {'run out', 'handled the ball', 'obstructing the field', 'hit the ball twice', 'timed out', 'retired out'}


def _rate(numerator, denominator, scale=1):
    return round(numerator * scale / denominator, 2) if denominator else None


def build_match_checks(envelope: dict) -> dict:
    envelope = apply_source_attribution(envelope)
    rows, errors = [], []
    if envelope.get('source_attribution', {}).get('rule') == 'source_conflict':
        errors.append('Source authority conflict: archived scorecard differs from current ICC;no interpretation accepted')
    group_size = envelope['meta']['balls_per_over']
    if type(group_size) is not int or group_size <= 0:
        raise ValueError('Invalid physical group size')
    for innings in envelope['innings']:
        number = innings['innings_number']
        source = innings['source_scorecard']
        if not isinstance(source, dict):
            raise ValueError('Invalid source scorecard shape')
        for key in ('runs', 'balls', 'wickets', 'wides', 'noballs', 'byes', 'legbyes', 'penalties', 'timed_out'):
            value = source.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'Invalid source scorecard quantity: {key}')
        deliveries = [d for over in innings['overs'] for d in over['deliveries']]
        batters, bowlers = {}, {}
        phases = defaultdict(lambda: {'deliveries': 0, 'legal_balls': 0, 'runs': 0})
        dismissed = set()
        team_wickets = 0
        for ordinal, delivery in enumerate(deliveries, 1):
            batter = batters.setdefault(delivery['batter_id'], {'runs': 0, 'balls_faced': 0, 'dismissals': 0})
            bowler = bowlers.setdefault(delivery['bowler_id'], {'runs_conceded': 0, 'actual_legal_balls': 0, 'wickets': 0})
            extras = delivery.get('statistical_extras', delivery['extras'])
            runs = delivery.get('statistical_runs', delivery['runs'])
            charged = runs['batter'] + extras.get('wides', 0) + extras.get('noballs', 0)
            observed_charge = delivery['runs']['batter'] + delivery['extras'].get('wides', 0) + delivery['extras'].get('noballs', 0)
            if delivery['delivery_ordinal'] != ordinal or delivery['bowler_runs'] != observed_charge:
                errors.append(f'Innings {number}: adapter delivery annotation mismatch')
            if 'statistical_bowler_runs' in delivery and delivery['statistical_bowler_runs'] != charged:
                errors.append(f'Innings {number}: statistical bowler annotation mismatch')
            batter['runs'] += runs['batter']
            batter['balls_faced'] += not bool(extras.get('wides', 0))
            bowler['runs_conceded'] += charged
            bowler['actual_legal_balls'] += delivery['is_legal']
            phase = phases[delivery['phase']]
            phase['deliveries'] += 1
            phase['legal_balls'] += delivery['is_legal']
            phase['runs'] += delivery['runs']['total']
            for event in delivery['wickets']:
                kind = event['kind']
                out = batters.setdefault(event['player_out_id'], {'runs': 0, 'balls_faced': 0, 'dismissals': 0})
                if kind in NON_DISMISSALS:
                    continue
                if kind not in BOWLER_DISMISSALS | OTHER_DISMISSALS:
                    errors.append(f'Innings {number}: Unrecognized dismissal {kind!r}')
                    continue
                if event['player_out_id'] in dismissed:
                    errors.append(f'Innings {number}: duplicate dismissal actor')
                    continue
                dismissed.add(event['player_out_id'])
                out['dismissals'] += 1
                team_wickets += 1
                bowler['wickets'] += kind in BOWLER_DISMISSALS
        source = innings['source_scorecard']
        recorded_timed_out = sum(w['kind'] == 'timed out' for d in deliveries for w in d['wickets'])
        reported_timed_out = source.get('timed_out')
        source_only = None if reported_timed_out is None else reported_timed_out - recorded_timed_out
        if source_only != innings['source_only_timed_out_count'] or (source_only is not None and source_only < 0):
            errors.append(f'Innings {number}: source-only timed-out annotation mismatch')
        team_wickets += source_only or 0
        penalty = innings['innings_penalty_runs']
        if type(penalty) is not int or penalty < 0:
            raise ValueError('Invalid innings penalty')
        delivery_runs = sum(d['runs']['total'] for d in deliveries)
        legal_balls = sum(d['is_legal'] for d in deliveries)
        team_runs = delivery_runs + penalty
        comparisons = {'runs': team_runs, 'balls': legal_balls, 'wickets': team_wickets}
        for key in ('wides', 'noballs', 'byes', 'legbyes'):
            comparisons[key] = sum(d.get('statistical_extras', d['extras']).get(key, 0) for d in deliveries)
        comparisons['penalties'] = penalty + sum(d.get('statistical_extras', d['extras']).get('penalty', 0) for d in deliveries)
        for field, derived in comparisons.items():
            if source.get(field) is not None and derived != source[field]:
                errors.append(f'Innings {number}: source {field} {source[field]} != derived {derived}')
        for field, derived in (('source_legal_balls', source.get('balls')), ('legal_balls', legal_balls), ('delivered_balls', len(deliveries))):
            if field in innings:
                value = innings[field]
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError(f'Invalid source counter: {field}')
                if value != derived:
                    errors.append(f'Innings {number}: {field} contradicts source/derived counter')
        figures = innings.get('source_player_figures')
        if figures:
            for pid, source_player in figures['batters'].items():
                got = batters.get(pid, {'runs': 0, 'balls_faced': 0})
                for field, target in (('runs', 'runs'), ('balls', 'balls_faced')):
                    if source_player[field] is not None and source_player[field] != got[target]:
                        errors.append(f'Innings {number}: source batter {pid} {field} mismatch')
            for pid, source_player in figures['bowlers'].items():
                got = bowlers.get(pid, {'runs_conceded': 0, 'actual_legal_balls': 0})
                for field, target in (('conceded', 'runs_conceded'), ('balls', 'actual_legal_balls')):
                    if source_player[field] is not None and source_player[field] != got[target]:
                        errors.append(f'Innings {number}: source bowler {pid} {field} mismatch')
        for batter in batters.values():
            batter['average'] = _rate(batter['runs'], batter['dismissals'])
            batter['strike_rate'] = _rate(batter['runs'], batter['balls_faced'], 100)
        for bowler in bowlers.values():
            bowler['economy'] = _rate(bowler['runs_conceded'], bowler['actual_legal_balls'], 6)
            bowler['bowling_average'] = _rate(bowler['runs_conceded'], bowler['wickets'])
            bowler['bowling_strike_rate'] = _rate(bowler['actual_legal_balls'], bowler['wickets'])
        rows.append({
            'innings_number': number, 'include_in_standard_stats': innings['include_in_standard_stats'],
            'delivery_runs': delivery_runs, 'innings_penalty_runs': penalty, 'team_runs': team_runs,
            'team_wickets': team_wickets, 'source_only_timed_out_count': source_only,
            'player_dismissal_coverage': 'unknown_source_only' if source_only is None else ('incomplete_source_only' if source_only else 'complete_observed'),
            'actual_legal_balls': legal_balls, 'source_legal_balls': source.get('balls'),
            'delivered_balls': len(deliveries), 'physical_group_size': group_size,
            'completed_groups': legal_balls // group_size, 'balls_in_group': legal_balls % group_size,
            'rate_basis_balls': 6, 'unknown_non_striker_deliveries': sum(d['non_striker_id'] is None for d in deliveries),
            'phase_counts': dict(phases), 'batters': batters, 'bowlers': bowlers,
            'source_player_coverage': 'verified_reviewed_figures' if figures else 'not_reviewed_per_player',
        })
    return {'match_id': envelope['match_id'], 'innings': rows, 'errors': errors}
