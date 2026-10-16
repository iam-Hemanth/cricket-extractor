"""Strict, lossless version-1 delivery records. No IO or database dependencies."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import math
import re
import struct
from types import MappingProxyType

VERSION = 1
RECORD_BYTES = 55
BLOCK_RECORDS = 35
DELIVERY_COLUMNS = (
    'match_id','innings_number','ordinal','group_number','event_ball_number',
    'batter_id','bowler_id','non_striker_id','legal','legal_before','legal_after',
    'batter_observed','extras_observed','total_observed','wides_observed',
    'noballs_observed','byes_observed','legbyes_observed','penalties_observed',
    'batter_scored','extras_scored','total_scored','wides_scored','noballs_scored',
    'byes_scored','legbyes_scored','penalties_scored','bowler_charge','phase',
    'nominal_phase','source_marker','source_unique_marker','source_group_number',
    'tactical','review',
)
_PARTS = ('batter','wides','noballs','byes','legbyes','penalties')
_TACTICS = {'shot':'shot_type','line':'pitch_line','length':'pitch_length',
            'control':'shot_control','zone':'wagon_zone'}
_TEXT = {'phase','nominal_phase','marker','unique_marker'}
_JSON = {*_TACTICS, 'review'}
_PACK = struct.Struct('>7H12B10H2hIB')


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} requires a native integer in [{low}, {high}]; no truncation')
    return value


def _identity(match_id, innings_number, ordinal):
    if type(match_id) is not str or re.fullmatch(r'[1-9][0-9]*',match_id) is None:
        raise ValueError('Canonical positive numeric match_id required')
    _integer(innings_number,1,32767,'innings_number')
    _integer(ordinal,1,32767,'ordinal')


def _native_json(value):
    typ = type(value)
    if value is None or typ in (str,int,bool): return
    if typ is float and math.isfinite(value): return
    if typ is list:
        for item in value: _native_json(item)
        return
    if typ is dict and all(type(key) is str for key in value):
        for item in value.values(): _native_json(item)
        return
    raise ValueError('Dictionary values require finite native JSON without coercion')


def _json_text(value):
    try:
        _native_json(value)
        return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    except (RecursionError,TypeError,OverflowError) as error:
        raise ValueError('Invalid or recursive JSON value') from error


@dataclass(frozen=True)
class Codebook:
    """Validated immutable copies of stable player and namespace dictionaries."""
    players: Mapping
    dictionaries: Mapping
    _players_by_key: Mapping = field(init=False,repr=False)
    _codes: Mapping = field(init=False,repr=False)

    def __post_init__(self):
        if not isinstance(self.players,Mapping) or not isinstance(self.dictionaries,Mapping):
            raise ValueError('Codebooks require mappings')
        players = dict(self.players)
        for player,key in players.items():
            if type(player) is not str or re.fullmatch(r'espn_[1-9][0-9]*',player) is None:
                raise ValueError('Canonical ESPN player identity required')
            _integer(key,1,32767,'player key')
        if len(set(players.values())) != len(players):
            raise ValueError('Duplicate player code')
        if set(self.dictionaries) - (_TEXT | _JSON):
            raise ValueError('Unknown dictionary namespace')
        dictionaries, codes = {}, {}
        for name in sorted(_TEXT | _JSON):
            entries = self.dictionaries.get(name,{})
            if not isinstance(entries,Mapping): raise ValueError('Dictionary requires a mapping')
            entries = dict(entries)
            for key,text in entries.items():
                _integer(key,1,4294967295 if name=='review' else 32767,'dictionary code')
                if type(text) is not str: raise ValueError('Dictionary text must be a string')
                if name in _JSON:
                    try: value = json.loads(text)
                    except (ValueError,RecursionError) as error:
                        raise ValueError('Invalid JSON dictionary text') from error
                    if value is None or _json_text(value) != text:
                        raise ValueError('JSON dictionary text must be canonical; null uses code zero')
            if len(set(entries.values())) != len(entries):
                raise ValueError('Duplicate dictionary value')
            dictionaries[name] = MappingProxyType(entries)
            codes[name] = MappingProxyType({v:k for k,v in entries.items()})
        object.__setattr__(self,'players',MappingProxyType(players))
        object.__setattr__(self,'dictionaries',MappingProxyType(dictionaries))
        object.__setattr__(self,'_players_by_key',MappingProxyType({v:k for k,v in players.items()}))
        object.__setattr__(self,'_codes',MappingProxyType(codes))

    def _code(self,name,value):
        if value is None: return 0
        if name in _JSON: text = _json_text(value)
        else:
            if type(value) is not str: raise ValueError(f'{name} must be text or null')
            text = value
        try: return self._codes[name][text]
        except (KeyError,TypeError) as error: raise ValueError(f'Unresolved {name} value') from error

    def _value(self,name,key):
        if key == 0: return None
        try: text = self.dictionaries[name][key]
        except KeyError as error: raise ValueError(f'Unresolved {name} code') from error
        return json.loads(text) if name in _JSON else text

    def _actor_key(self,player,nullable=False):
        if player is None and nullable: return 0
        if type(player) is not str: raise ValueError('Player ID must be canonical text')
        try: return self.players[player]
        except KeyError as error: raise ValueError('Unresolved player ID') from error

    def _actor(self,key,nullable=False):
        if key == 0 and nullable: return None
        try: return self._players_by_key[key]
        except KeyError as error: raise ValueError('Unresolved player code') from error


def encode_delivery(row: Mapping, codebook: Codebook) -> bytes:
    if not isinstance(row,Mapping) or set(row) != set(DELIVERY_COLUMNS):
        raise ValueError('Exactly the version-1 delivery fields are required')
    if not isinstance(codebook,Codebook): raise ValueError('Validated Codebook required')
    _identity(row['match_id'],row['innings_number'],row['ordinal'])
    components = []
    for kind in ('observed','scored'):
        values = [_integer(row[f'{part}_{kind}'],0,255,f'{part}_{kind}') for part in _PARTS]
        extras = _integer(row[f'extras_{kind}'],0,1275,f'extras_{kind}')
        total = _integer(row[f'total_{kind}'],0,1530,f'total_{kind}')
        if extras != sum(values[1:]) or total != sum(values):
            raise ValueError('Run totals cannot be reconstructed exactly')
        components.extend(values)
    legal = row['legal']
    before = _integer(row['legal_before'],0,65535,'legal_before')
    after = _integer(row['legal_after'],0,65535,'legal_after')
    if type(legal) is not bool or legal != (components[1]==0 and components[2]==0) or after != before+int(legal):
        raise ValueError('Observed legality and actual legal clock must agree')
    charge = _integer(row['bowler_charge'],0,765,'bowler_charge')
    if charge != sum(components[6:9]): raise ValueError('Bowler charge is inconsistent')
    tactical = row['tactical']
    flags = 0
    if tactical is None:
        flags = 3
        tactic_codes, x, y = [0]*5, 0, 0
    else:
        if type(tactical) is not dict or set(tactical) != {*_TACTICS.values(),'wagon_coords'}:
            raise ValueError('Unknown tactical shape; codec revision required')
        tactic_codes = [codebook._code(name,tactical[key]) for name,key in _TACTICS.items()]
        coords = tactical['wagon_coords']
        if coords is None: flags, x, y = 2, 0, 0
        else:
            if type(coords) is not list or len(coords)!=2: raise ValueError('Exactly two native coordinates required')
            x,y = [_integer(v,-32768,32767,'wagon coordinate') for v in coords]
    source_group = row['source_group_number']
    source_group = 65535 if source_group is None else _integer(source_group,0,65534,'source_group_number')
    values = [
        _integer(row['group_number'],0,65535,'group_number'),
        _integer(row['event_ball_number'],1,65535,'event_ball_number'),
        codebook._actor_key(row['batter_id']),codebook._actor_key(row['bowler_id']),
        codebook._actor_key(row['non_striker_id'],True),before,after,*components,
        codebook._code('phase',row['phase']),codebook._code('nominal_phase',row['nominal_phase']),
        codebook._code('marker',row['source_marker']),codebook._code('unique_marker',row['source_unique_marker']),
        source_group,*tactic_codes,x,y,codebook._code('review',row['review']),flags,
    ]
    return _PACK.pack(*values)


def decode_delivery(payload: bytes, *, version: int, match_id: str,
                    innings_number: int, ordinal: int, codebook: Codebook) -> dict:
    if type(version) is not int or version != VERSION: raise ValueError('Unsupported codec version')
    if type(payload) is not bytes or len(payload)!=RECORD_BYTES: raise ValueError('Invalid delivery byte length/type')
    if not isinstance(codebook,Codebook): raise ValueError('Validated Codebook required')
    _identity(match_id,innings_number,ordinal)
    v = _PACK.unpack(payload)
    group,ball,batter,bowler,partner,before,after = v[:7]
    row = dict(match_id=match_id,innings_number=innings_number,ordinal=ordinal,
        group_number=group,event_ball_number=ball,batter_id=codebook._actor(batter),
        bowler_id=codebook._actor(bowler),non_striker_id=codebook._actor(partner,True),
        legal=v[8]==0 and v[9]==0,legal_before=before,legal_after=after)
    for pos,kind in enumerate(('observed','scored')):
        values = v[7+6*pos:13+6*pos]
        row.update({f'{part}_{kind}':value for part,value in zip(_PARTS,values)})
        row[f'extras_{kind}'],row[f'total_{kind}'] = sum(values[1:]),sum(values)
    row['bowler_charge'] = sum(v[13:16])
    row.update(phase=codebook._value('phase',v[19]),nominal_phase=codebook._value('nominal_phase',v[20]),
        source_marker=codebook._value('marker',v[21]),source_unique_marker=codebook._value('unique_marker',v[22]),
        source_group_number=None if v[23]==65535 else v[23],review=codebook._value('review',v[31]))
    flags = v[32]
    if flags & ~3: raise ValueError('Reserved record flags are set')
    tactics = {key:codebook._value(name,v[24+i]) for i,(name,key) in enumerate(_TACTICS.items())}
    tactics['wagon_coords'] = None if flags & 2 else [v[29],v[30]]
    row['tactical'] = None if flags & 1 else tactics
    if encode_delivery(row,codebook) != payload:
        raise ValueError('Noncanonical record, hidden null payload or inconsistent clock')
    return row


def encode_block(rows: Sequence[Mapping], codebook: Codebook) -> bytes:
    if not isinstance(rows,Sequence) or not 1 <= len(rows) <= BLOCK_RECORDS:
        raise ValueError('Blocks require 1–35 delivery mappings')
    payload = []
    first = None
    for pos,row in enumerate(rows):
        encoded = encode_delivery(row,codebook)
        if first is None: first = row
        if (row['match_id'],row['innings_number'],row['ordinal']) != (first['match_id'],first['innings_number'],first['ordinal']+pos):
            raise ValueError('Block identity/order must be contiguous in one innings')
        payload.append(encoded)
    return b''.join(payload)


def decode_block(payload: bytes, *, version: int, match_id: str,
                 innings_number: int, start_ordinal: int, records: int,
                 codebook: Codebook) -> list[dict]:
    _integer(records,1,BLOCK_RECORDS,'records')
    _identity(match_id,innings_number,start_ordinal)
    _integer(start_ordinal+records-1,1,32767,'last ordinal')
    if type(payload) is not bytes or len(payload)!=records*RECORD_BYTES:
        raise ValueError('Invalid block byte length/type')
    return [decode_delivery(payload[i*RECORD_BYTES:(i+1)*RECORD_BYTES],version=version,
        match_id=match_id,innings_number=innings_number,ordinal=start_ordinal+i,
        codebook=codebook) for i in range(records)]
