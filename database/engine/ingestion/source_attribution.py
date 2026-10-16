"""Evidence-bound statistical views; physical source observations never change."""
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

EVIDENCE_PATH = Path(__file__).with_name('attribution_evidence.json')
EVIDENCE_SHA256 = 'd5de20b086f2b8666f8cafe3ecbc2cf7ae7d4c7efd000abfc31e90d04351788b'
STAT_FIELDS = ('statistical_runs', 'statistical_extras', 'statistical_bowler_runs')


def _same(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def _put(record, key, value):
    if key in record and not _same(record[key], value):
        raise ValueError(f'Conflicting source-owned attribution: {key}')
    record[key] = copy.deepcopy(value)


@lru_cache(maxsize=1)
def _frozen_cases():
    raw = EVIDENCE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EVIDENCE_SHA256:
        raise ValueError('Attribution evidence differs from independently pinned bytes')
    data = json.loads(raw)
    if data['version'] != 'extras_evidence_v1':
        raise ValueError('Unsupported attribution evidence')
    return MappingProxyType({mid: json.dumps(entry, allow_nan=False) for mid, entry in data['matches'].items()})


def load_attribution_evidence():
    """Return isolated public copies;never expose the cached authority."""
    return {mid: json.loads(raw) for mid, raw in _frozen_cases().items()}


def _scored(delivery, runs, extras):
    if runs['total'] != delivery['runs']['total'] or runs['batter'] + sum(extras.values()) != runs['total']:
        raise ValueError('Attribution would change team total')
    if runs['extras'] != sum(extras.values()):
        raise ValueError('Attribution components contradict extras total')
    if bool(extras.get('wides', 0) or extras.get('noballs', 0)) != (not delivery['is_legal']):
        raise ValueError('Attribution would change legal ball status')
    _put(delivery, 'statistical_runs', runs)
    _put(delivery, 'statistical_extras', extras)
    _put(delivery, 'statistical_bowler_runs', runs['batter'] + extras.get('wides', 0) + extras.get('noballs', 0))


def apply_source_attribution(envelope: dict) -> dict:
    frozen = _frozen_cases().get(envelope['match_id'])
    evidence = json.loads(frozen) if frozen is not None else None
    if evidence is None:
        if 'source_attribution' in envelope or any('source_player_figures' in i for i in envelope['innings']):
            raise ValueError('Unreviewed source attribution')
        if any(any(k in d for k in STAT_FIELDS) for i in envelope['innings'] for o in i['overs'] for d in o['deliveries']):
            raise ValueError('Unreviewed statistical delivery')
        return envelope
    if envelope['source_sha256'] != evidence['source_sha256']:
        raise ValueError('Attribution input SHA mismatch')
    meta = envelope['meta']
    if meta['record_class'] != evidence['record_class'] or meta['dates'][0] != evidence['date']:
        raise ValueError('Attribution source classification/date mismatch')
    if len(envelope['innings']) != len(evidence['innings']):
        raise ValueError('Attribution innings coverage mismatch')
    result = copy.deepcopy(envelope)
    _put(result, 'source_attribution', {'rule': evidence['rule'], 'evidence_sha256': EVIDENCE_SHA256,
                                     'raw_scorecard_compressed_sha256': evidence['raw_scorecard_compressed_sha256']})
    allowed = set()
    for index, (innings, figures) in enumerate(zip(result['innings'], evidence['innings'])):
        if innings['innings_number'] != figures['innings_number'] or not _same(innings['source_scorecard'], figures['source_scorecard']):
            raise ValueError('Attribution scorecard/innings identity mismatch')
        for role in ('batters', 'bowlers'):
            for pid, row in figures[role].items():
                if pid not in result['players']:
                    raise ValueError('Scorecard actor missing from participants')
                if any(v is not None and (type(v) is not int or v < 0) for v in row.values()):
                    raise ValueError('Invalid per-player source figure')
        _put(innings, 'source_player_figures', {role: figures[role] for role in ('batters', 'bowlers')})
        if evidence['rule'] == 'icc_pre_2017_no_ball_byes':
            if meta['record_class'] not in {'test', 'odi', 't20i'} or evidence['date'] >= '2017-09-28':
                raise ValueError('Legacy rule lacks historical international scope')
            for oi, over in enumerate(innings['overs']):
                for di, delivery in enumerate(over['deliveries']):
                    extras = delivery['extras']
                    transfer = extras.get('byes', 0) + extras.get('legbyes', 0)
                    if extras.get('noballs', 0) and transfer:
                        if delivery['runs']['batter']:
                            raise ValueError('Legacy no-ball byes contradict batter runs')
                        statistical = dict(extras)
                        statistical['noballs'] += transfer
                        statistical.pop('byes', None)
                        statistical.pop('legbyes', None)
                        _scored(delivery, delivery['runs'], statistical)
                        allowed.add((index, oi, di))
    for ix, innings in enumerate(result['innings']):
        for oi, over in enumerate(innings['overs']):
            for di, delivery in enumerate(over['deliveries']):
                if (ix, oi, di) not in allowed and any(k in delivery for k in STAT_FIELDS):
                    raise ValueError('Statistical annotation outside verified scope')
    return result
