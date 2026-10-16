"""Authenticated fresh compact imports. Explicit private target; no live DB fallback."""
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

from psycopg2 import sql
from db.source_connection import connect_proof,validate_descriptor,assert_proof_session
from db.compact_connection import SCHEMA,SCHEMA_PATH,apply_compact_schema
from ingestion import source_store as source
from ingestion.compact_codec import Codebook,encode_block,BLOCK_RECORDS

ROOT=Path(__file__).resolve().parents[1]
VARIABLE_FIELDS={'source_refs','team_ids','participant_roles'}
TACTICS={'shot':'shot_type','line':'pitch_line','length':'pitch_length','control':'shot_control','zone':'wagon_zone'}
JSON_NAMES={*TACTICS,'review'}
TABLE_COLUMNS={
    'players':source.COLUMNS['players'],'player_aliases':source.COLUMNS['player_aliases'],
    'player_map':('key','player_id'),'matches':source.COLUMNS['matches'],
    'match_teams':source.COLUMNS['match_teams'],'match_map':('key','match_id'),
    'participant_profiles':('profile_key','source_profile'),
    'participant_records':('match_id','player_id','profile_key','variable_facts'),
    'lineup_assertions':source.COLUMNS['lineup_assertions'],'lineup_members':source.COLUMNS['lineup_members'],
    'participation_events':source.COLUMNS['participation_events'],
    'innings':source.COLUMNS['innings']+('match_key',),
    'dictionary':('name','id','value'),
    'blocks':('match_key','innings_number','start_ordinal','records','version','payload','payload_sha256'),
    'actor_innings':('role','player_key','match_key','innings_number','runs','balls','dismissals'),
    'dismissal_events':source.COLUMNS['dismissal_events'],
    'source_documents':source.COLUMNS['source_documents'],'source_holds':source.COLUMNS['source_holds'],
}
IMPORT_CODE_PATHS=source.IMPORT_CODE_PATHS+(
    'ingestion/compact_codec.py','ingestion/compact_store.py',
    'db/compact_schema.sql','db/compact_connection.py',
    'db/compact_verify.py',
)
LOADED_CODE={name:source._sha(ROOT/name) for name in IMPORT_CODE_PATHS}


def _code_stable():
    if any(source._sha(ROOT/name)!=digest for name,digest in LOADED_CODE.items()):
        raise ValueError('Loaded compact import implementation changed; restart before importing')


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _split_participant(values):
    row=dict(zip(source.COLUMNS['participants'],values)); facts=row['source_facts']
    if type(facts) is not dict:raise ValueError('Participant source facts must be an object')
    if (row['source_name']!=facts.get('name') or row['roles']!=facts.get('participant_roles',[])
            or row['team_source_ids']!=facts.get('team_ids',[])):
        raise ValueError('Participant projection cannot be reconstructed exactly')
    return ({k:v for k,v in facts.items() if k not in VARIABLE_FIELDS},
            {k:v for k,v in facts.items() if k in VARIABLE_FIELDS})


def _source_records(payload,manifest):
    """Hash every consumed byte and enforce exact unique authority coverage."""
    allowed=set(manifest['record_ids']); seen=set(); digest=hashlib.sha256()
    with (payload/'matches.jsonl').open('rb') as stream:
        for raw in stream:
            digest.update(raw); envelope=json.loads(raw); mid=envelope['match_id']
            if mid in seen or mid not in allowed:raise ValueError('Duplicate or unverified payload match')
            seen.add(mid)
            yield envelope,raw
    if seen!=allowed or digest.hexdigest()!=manifest['payload_sha256']['matches.jsonl']:
        raise ValueError('Consumed source identities or bytes differ from pinned payload')


def _collect(payload,manifest,requested,run_id):
    players,aliases,profiles={},{},{}
    dictionaries={name:{} for name in ('phase','nominal_phase','marker','unique_marker',*JSON_NAMES)}
    def register(name,value):
        if value is None:return
        text=_json(value) if name in JSON_NAMES else value
        if type(text) is not str:raise ValueError('Text dictionary value requires a native string')
        bucket=dictionaries[name]
        if text not in bucket:bucket[text]=len(bucket)+1
    for envelope,raw in _source_records(payload,manifest):
        if envelope['match_id'] not in requested:continue
        rows=source.match_rows(envelope,raw,run_id)
        for value in rows['players']:
            pid,public,name=value
            if pid in players and players[pid][1]!=public:raise ValueError('Conflicting canonical player identity')
            if pid not in players or players[pid][2] is None:players[pid]=value
        for value in rows['player_aliases']:
            key=value[:2]
            if key in aliases and aliases[key]!=value:raise ValueError('Conflicting player alias')
            aliases[key]=value
        for value in rows['participants']:
            common,_=_split_participant(value); text=_json(common)
            if text not in profiles:profiles[text]=len(profiles)+1
        for value in rows['deliveries']:
            row=dict(zip(source.COLUMNS['deliveries'],value))
            for name,column in [('phase','phase'),('nominal_phase','nominal_phase'),('marker','source_marker'),('unique_marker','source_unique_marker'),('review','review')]:
                register(name,row[column])
            tactical=row['tactical']
            if tactical is not None:
                if type(tactical) is not dict or set(tactical)!={*TACTICS.values(),'wagon_coords'}:
                    raise ValueError('Unknown tactical fields require a codec revision')
                for name,column in TACTICS.items():register(name,tactical[column])
    player_map={pid:i for i,pid in enumerate(sorted(players),1)}
    match_map={mid:i for i,mid in enumerate(sorted(requested),1)}
    if len(match_map)>32767:raise ValueError('Match map needs a wider explicit representation')
    book=Codebook(player_map,{name:{key:text for text,key in entries.items()} for name,entries in dictionaries.items()})
    codebook_bytes=_json({'players':player_map,'matches':match_map,
        'dictionaries':{n:dict(v) for n,v in book.dictionaries.items()},'codec_version':1}).encode()
    return players,aliases,profiles,match_map,book,hashlib.sha256(codebook_bytes).hexdigest()


class _Stage:
    def __init__(self,path):
        self.path=path
        self.streams={name:(path/(name+'.csv')).open('xb') for name in TABLE_COLUMNS}
        self.digests={name:hashlib.sha256() for name in TABLE_COLUMNS}
        self.bytes={name:0 for name in TABLE_COLUMNS}
        self.counts={name:0 for name in TABLE_COLUMNS}

    def write(self,table,row):
        data=source.csv_line(row).encode('utf-8')
        self.streams[table].write(data);self.digests[table].update(data)
        self.bytes[table]+=len(data);self.counts[table]+=1

    def close(self):
        for stream in self.streams.values():stream.close()


def _stage_source(stage,payload,manifest,requested,run_id,maps):
    players,aliases,profiles,match_map,book,_=maps; delivery_count=0
    for row in players.values():stage.write('players',row)
    for row in aliases.values():stage.write('player_aliases',row)
    for pid,key in book.players.items():stage.write('player_map',(key,pid))
    for text,key in profiles.items():stage.write('participant_profiles',(key,json.loads(text)))
    for name,entries in book.dictionaries.items():
        for key,text in entries.items():stage.write('dictionary',(name,key,text))
    for envelope,raw in _source_records(payload,manifest):
        mid=envelope['match_id']
        if mid not in requested:continue
        rows=source.match_rows(envelope,raw,run_id)
        for original in rows['participants']:
            common,variable=_split_participant(original)
            stage.write('participant_records',(mid,original[1],profiles[_json(common)],variable))
        for original in rows['innings']:stage.write('innings',original+(match_map[mid],))
        stage.write('match_map',(match_map[mid],mid))
        for table,values in rows.items():
            if table in ('players','player_aliases','participants','innings','deliveries'):continue
            for value in values:stage.write(table,value)
        block=[]; prior=None
        def flush():
            if not block:return
            blob=encode_block(block,book)
            stage.write('blocks',(match_map[mid],block[0]['innings_number'],block[0]['ordinal'],len(block),1,blob,hashlib.sha256(blob).digest()))
        for value in rows['deliveries']:
            row=dict(zip(source.COLUMNS['deliveries'],value))
            if prior and row['innings_number']==prior['innings_number']:
                if row['ordinal']!=prior['ordinal']+1 or row['legal_before']!=prior['legal_after']:
                    raise ValueError('Source delivery identity/clock discontinuity')
            elif row['ordinal']!=1 or row['legal_before']!=0:
                raise ValueError('Innings must start with ordinal one and actual clock zero')
            if block and (len(block)==BLOCK_RECORDS or row['innings_number']!=block[0]['innings_number']):
                flush();block=[]
            block.append(row);prior=row;delivery_count+=1
        flush()
    return delivery_count


def _stage_checks(stage,payload,manifest,requested,maps):
    _,_,_,match_map,book,_=maps; holds={}; seen=set(); consumed=hashlib.sha256()
    expected={'innings':{},'phases':{}}
    with (payload/'checks.jsonl').open('rb') as stream:
        for raw in stream:
            consumed.update(raw); check=json.loads(raw);mid=check['match_id']
            if mid in seen:raise ValueError('Duplicate source checks')
            seen.add(mid)
            if check['errors']:holds[mid]=check['errors']
            if mid not in requested:continue
            if check['errors']:raise ValueError('Held match entered selection')
            for inn in check['innings']:
                identity=(mid,inn['innings_number'])
                expected['innings'][identity]=(inn['team_runs'],inn['actual_legal_balls'],inn['team_wickets'],
                    inn['delivered_balls'],inn['innings_penalty_runs'],inn['unknown_non_striker_deliveries'])
                for phase,values in inn['phase_counts'].items():
                    expected['phases'][identity+(phase,)]=(values['deliveries'],values['legal_balls'],values['runs'])
                for role,name,columns in [(1,'batters',('runs','balls_faced','dismissals')),(2,'bowlers',('runs_conceded','actual_legal_balls','wickets'))]:
                    for pid,counters in inn[name].items():
                        values=[counters[col] for col in columns]
                        if any(type(v) is not int or not 0<=v<=2147483647 for v in values):raise ValueError('Non-native actor counters')
                        stage.write('actor_innings',(role,book.players[pid],match_map[mid],inn['innings_number'],*values))
    if consumed.hexdigest()!=manifest['payload_sha256']['checks.jsonl'] or seen!=set(manifest['record_ids'])|set(manifest['held_ids']):
        raise ValueError('Consumed check identities/bytes differ')
    if set(holds)!=set(manifest['held_ids']) or len(holds)!=manifest['held_records']:raise ValueError('Hold ledger differs')
    for mid,reason in holds.items():stage.write('source_holds',(mid,'extras_or_source_authority',reason,source.SUBSET_SHA256))
    return expected


def _validate_source_counters(conn,expected):
    definitions=[('innings','v_innings_totals',2,'match_id,innings_number,team_runs,actual_legal_balls,team_wickets,delivered_balls,penalty_runs,unknown_non_strikers'),
                 ('phases','v_phase_totals',3,'match_id,innings_number,phase,delivered_balls,actual_legal_balls,runs')]
    with conn.cursor() as c:
        for name,view,width,columns in definitions:
            c.execute(f'SELECT {columns} FROM {SCHEMA}.{view}')
            actual={tuple(r[:width]):tuple(r[width:]) for r in c.fetchall()}
            if actual!=expected[name]:raise ValueError('Independent SQL '+name+' counters differ from authenticated checks')


def validate_sql_counters(conn):
    """Recompute independently from SQL-decoded facts, not the cached checks."""
    statement=f'''WITH expected AS(
      SELECT 1::smallint AS role,p.key AS player_key,m.key AS match_key,v.innings_number::smallint AS innings_number,
             v.runs::integer AS runs,v.balls_faced::integer AS balls,v.dismissals::integer AS dismissals
        FROM {SCHEMA}.v_batter_totals v JOIN {SCHEMA}.player_map p USING(player_id) JOIN {SCHEMA}.match_map m USING(match_id)
      UNION ALL
      SELECT 2::smallint,p.key,m.key,v.innings_number::smallint,v.runs_conceded::integer,v.actual_legal_balls::integer,v.wickets::integer
        FROM {SCHEMA}.v_bowler_totals v JOIN {SCHEMA}.player_map p USING(player_id) JOIN {SCHEMA}.match_map m USING(match_id)),
      missing AS(SELECT * FROM expected EXCEPT ALL SELECT * FROM {SCHEMA}.actor_innings),
      extra AS(SELECT * FROM {SCHEMA}.actor_innings EXCEPT ALL SELECT * FROM expected)
      SELECT (SELECT COUNT(*) FROM missing)+(SELECT COUNT(*) FROM extra)'''
    with conn.cursor() as c:
        c.execute(statement)
        if c.fetchone()[0]:raise ValueError('Independent SQL actor counters differ from authenticated checks')
        c.execute(f'''WITH sequence AS(SELECT match_key,innings_number,start_ordinal,records,
            COALESCE(LAG(start_ordinal+records) OVER(PARTITION BY match_key,innings_number ORDER BY start_ordinal),1) AS expected,
            LAG(payload) OVER(PARTITION BY match_key,innings_number ORDER BY start_ordinal) AS prior,
            LAG(records) OVER(PARTITION BY match_key,innings_number ORDER BY start_ordinal) AS prior_records,
            payload FROM {SCHEMA}.blocks)
          SELECT COUNT(*) FROM sequence WHERE start_ordinal<>expected OR
            ((get_byte(payload,10)*256+get_byte(payload,11))<>
            CASE WHEN prior IS NULL THEN 0 ELSE get_byte(prior,(prior_records-1)*55+12)*256+get_byte(prior,(prior_records-1)*55+13) END)''')
        if c.fetchone()[0]:raise ValueError('Stored block identity/clock discontinuity')
        c.execute(f'''SELECT COUNT(*) FROM {SCHEMA}.innings i LEFT JOIN
             (SELECT match_id,innings_number,COUNT(*) FILTER(WHERE legal) AS actual FROM {SCHEMA}.deliveries GROUP BY match_id,innings_number) d
             USING(match_id,innings_number) WHERE i.actual_legal_balls<>COALESCE(d.actual,0)''')
        if c.fetchone()[0]:raise ValueError('Innings actual clock differs from SQL-decoded deliveries')


def import_compact_subset(descriptor:Path,manifest_path:Path,payload_dir:Path,
                          staging:Path,match_ids:list[str]|None=None)->dict:
    started=time.monotonic();validate_descriptor(descriptor);_code_stable()
    manifest=source.validate_subset(manifest_path,payload_dir)
    ids=manifest['record_ids'] if match_ids is None else match_ids
    if type(ids) is not list or not ids or any(type(mid) is not str for mid in ids) or len(ids)!=len(set(ids)) or not set(ids)<=set(manifest['record_ids']):
        raise ValueError('Not a unique verified selection')
    requested=set(ids); mode='full' if match_ids is None else 'cohort'
    if f'_{mode}_' not in validate_descriptor(descriptor)['database']:raise ValueError('Wrong cohort/full target')
    if not staging.is_absolute() or staging.is_symlink() or staging.resolve()!=staging:
        raise ValueError('Staging path must be canonical and explicit')
    if staging.exists():raise FileExistsError('Fresh staging directory required')
    staging.mkdir(mode=0o700);run_id=str(uuid.uuid4())
    maps=_collect(payload_dir,manifest,requested,run_id)
    stage=_Stage(staging)
    try:
        deliveries=_stage_source(stage,payload_dir,manifest,requested,run_id,maps)
        expected_counters=_stage_checks(stage,payload_dir,manifest,requested,maps)
    finally:stage.close()
    _code_stable(); session=connect_proof(descriptor); committed=False
    try:
        conn=session.connection
        with conn.cursor() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_compact_import'))")
        apply_compact_schema(session)
        with conn.cursor() as c:
            for table in ('import_runs',*TABLE_COLUMNS):
                c.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM {}.{})').format(sql.Identifier(SCHEMA),sql.Identifier(table)))
                if c.fetchone()[0]:raise ValueError('Compact facts are nonempty; refuse overwrite')
            c.execute(f'INSERT INTO {SCHEMA}.import_runs(run_id,manifest_sha256,mode,expected_matches,held_count,full_admitted_snapshot_ready,provenance) VALUES(%s,%s,%s,%s,%s,false,%s)',
                      (run_id,source.SUBSET_SHA256,mode,len(ids),manifest['held_records'],json.dumps(manifest)))
            for table,columns in TABLE_COLUMNS.items():
                command=sql.SQL("COPY {}.{} ({}) FROM STDIN WITH(FORMAT CSV,NULL '\\N')").format(sql.Identifier(SCHEMA),sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)))
                fd=os.open(staging/(table+'.csv'),os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Staging must be a regular file')
                    consumed=source._DigestReader(stream);c.copy_expert(command.as_string(c),consumed)
                if consumed.digest.hexdigest()!=stage.digests[table].hexdigest() or consumed.size!=stage.bytes[table]:
                    raise ValueError('Consumed COPY stream differs from generated projection')
            counts={}
            for table in TABLE_COLUMNS:
                c.execute(sql.SQL('SELECT COUNT(*) FROM {}.{}').format(sql.Identifier(SCHEMA),sql.Identifier(table)));counts[table]=c.fetchone()[0]
            if counts!=stage.counts:raise ValueError('Stored compact table counts differ')
            c.execute(f'SELECT SUM(records) FROM {SCHEMA}.blocks')
            if (c.fetchone()[0] or 0)!=deliveries:raise ValueError('Decoded delivery count differs')
        validate_sql_counters(conn);_validate_source_counters(conn,expected_counters)
        assert_proof_session(session);_code_stable()
        receipt={'run_id':run_id,'mode':mode,'target':dict(session.descriptor),'manifest_sha256':source.SUBSET_SHA256,
            'stored_matches':len(ids),'deliveries':deliveries,'table_counts':counts,
            'full_admitted_snapshot_ready':False,'storage_import_passed':True,'committed':True,
            'schema_sha256':source._sha(SCHEMA_PATH),'source_code_sha256':LOADED_CODE,
            'codec_version':1,'codebook_sha256':maps[-1],
            'staging_sha256':{t:h.hexdigest() for t,h in stage.digests.items()},'staging_bytes':stage.bytes,
            'elapsed_seconds':round(time.monotonic()-started,3),
            'limits':['Fresh compact storage proof; no complete consumers, daily correction or hosted proof','18 real source holds remain excluded; controlled fixtures use their explicit held count']}
        pending=staging/'import-receipt.pending.json'
        with pending.open('x') as stream:stream.write(json.dumps(receipt,indent=2)+'\n')
        with conn.cursor() as c:c.execute(f'UPDATE {SCHEMA}.import_runs SET provenance=%s WHERE run_id=%s',(json.dumps({'manifest':manifest,'receipt':receipt}),run_id))
        assert_proof_session(session);_code_stable();conn.commit();committed=True
        try:os.replace(pending,staging/'import-receipt.json')
        except OSError as error:return {**receipt,'receipt_published':False,'receipt_export_error':str(error)}
        return {**receipt,'receipt_published':True}
    except Exception:
        if not committed:session.connection.rollback()
        raise
    finally:session.close()
