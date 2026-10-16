"""Lossless projections and atomic COPY imports for the verified private subset."""
import gzip
import hashlib
import json
import os
import stat
from pathlib import Path
import time
import uuid

from psycopg2 import sql
from db.source_connection import connect_proof,assert_proof_session,apply_schema,SCHEMA,SCHEMA_PATH
from ingestion.source_checks import NON_DISMISSALS,BOWLER_DISMISSALS,OTHER_DISMISSALS

ROOT=Path(__file__).resolve().parents[1]
SUBSET_SHA256='50a20f4303c8803da3258dbce166842ac2bd9d4f7c4d33c06d931b0adfe72afe'
COLUMNS={
 'players':('player_id','espn_id','name'),
 'player_aliases':('source','source_id','player_id'),
 'matches':('match_id','run_id','date','season','format_bucket','record_class','playing_format','balls_per_group','competition','trophy_id','venue','venue_country','city','gender','outcome','toss','meta'),
 'match_teams':('match_id','team_name'),
 'participants':('match_id','player_id','source_name','roles','team_source_ids','source_facts'),
 'lineup_assertions':('match_id','team_name','status'),
 'lineup_members':('match_id','team_name','position','player_id'),
 'participation_events':('match_id','event_ordinal','event_type','incoming_player_id','outgoing_player_id','source_facts'),
 'innings':('match_id','innings_number','batting_team','bowling_team','standard_stats','super_over','innings_kind','penalty_runs','actual_legal_balls','source_scorecard_balls','source_only_timed_out','source_scorecard','metadata'),
 'deliveries':('match_id','innings_number','ordinal','group_number','event_ball_number','batter_id','bowler_id','non_striker_id','legal','legal_before','legal_after',
 'batter_observed','extras_observed','total_observed','wides_observed','noballs_observed','byes_observed','legbyes_observed','penalties_observed',
 'batter_scored','extras_scored','total_scored','wides_scored','noballs_scored','byes_scored','legbyes_scored','penalties_scored','bowler_charge','phase','nominal_phase','source_marker','source_unique_marker','source_group_number','tactical','review'),
 'dismissal_events':('match_id','innings_number','event_key','delivery_ordinal','player_out_id','kind','source_only','quantity','team_wickets','batter_dismissals','bowler_wickets','fielders','source_facts'),
 'source_documents':('match_id','payload_gzip','payload_sha256','source_sha256'),
 'source_holds':('match_id','stage','reason','manifest_sha256'),
}


def _sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _quantity(value,nullable=False):
    if value is None and nullable:return None
    if type(value) is not int or value<0:raise ValueError('Expected a nonnegative integer quantity')
    return value



def _positive_integer(value):
    if type(value) is not int or value<=0:raise ValueError('Expected a positive native integer')
    return value


def _boolean(value):
    if type(value) is not bool:raise ValueError('Expected a native boolean')
    return value


class _DigestReader:
    """Hash the exact binary stream consumed by COPY, rather than a pre-read."""
    def __init__(self,stream):self.stream=stream;self.digest=hashlib.sha256();self.size=0
    def read(self,size=-1):
        data=self.stream.read(size);self.digest.update(data);self.size+=len(data);return data


def csv_line(row):
    fields=[]
    for value in row:
        if value is None:fields.append(r'\N');continue
        if isinstance(value,bytes):value=r'\x'+value.hex()
        elif isinstance(value,(dict,list)):value=json.dumps(value,separators=(',',':'),allow_nan=False)
        elif type(value) is bool:value='true' if value else 'false'
        else:value=str(value)
        fields.append('"'+value.replace('"','""')+'"')
    return ','.join(fields)+'\n'


def match_rows(envelope:dict,raw_bytes:bytes,run_id:str)->dict:
    if json.dumps(json.loads(raw_bytes),sort_keys=True,allow_nan=False)!=json.dumps(envelope,sort_keys=True,allow_nan=False):
        raise ValueError('Stored document does not describe projected facts')
    rows={table:[] for table in COLUMNS}
    def add(table,*values):
        if len(values)!=len(COLUMNS[table]):raise ValueError('Projection column mismatch')
        rows[table].append(tuple(values))
    mid=envelope['match_id'];meta=envelope['meta'];players=envelope['players']
    if type(mid) is not str or not mid.isascii() or not mid.isdigit():raise ValueError('Expected canonical numeric match ID')
    _positive_integer(meta['balls_per_over'])
    def actor(pid,nullable=False):
        if pid is None and nullable:return
        if pid not in players:raise ValueError('Actor not in match participants')
    for pid,p in players.items():
        _positive_integer(p['espn_id'])
        if pid!=p['player_id'] or pid!=f"espn_{p['espn_id']}":raise ValueError('Canonical player identity mismatch')
        add('players',pid,p['espn_id'],p.get('name'))
        add('participants',mid,pid,p.get('name'),p.get('participant_roles',[]),p.get('team_ids',[]),p)
    for alias,pid in envelope.get('player_aliases',{}).items():actor(pid);add('player_aliases','cricsheet',alias,pid)
    dates=meta.get('dates') or []
    add('matches',mid,run_id,dates[0] if dates else None,str(meta['season']) if meta.get('season') is not None else None,
        meta['format_bucket'],meta['record_class'],meta['playing_format'],meta['balls_per_over'],meta.get('competition'),meta.get('source_competition_trophy_id'),
        meta.get('venue'),meta.get('venue_country'),meta.get('city'),meta.get('gender'),envelope['outcome'],envelope['toss'],meta)
    teams={k for k in envelope['playing_xi'] if not k.startswith('_')}
    teams.update(t for i in envelope['innings'] for t in (i['batting_team'],i['bowling_team']) if t)
    if len(teams)!=2:raise ValueError('Exactly two source teams are required')
    for team in sorted(teams):add('match_teams',mid,team)
    for team,entries in envelope['playing_xi'].items():
        if team.startswith('_'):continue
        add('lineup_assertions',mid,team,meta.get('playing_xi_status',{}).get(team,'unknown'))
        for index,p in enumerate(entries,1):
            pid=p['player_id'] if isinstance(p,dict) else p;actor(pid);add('lineup_members',mid,team,index,pid)
    for index,e in enumerate(envelope['participation_events'],1):
        incoming,outgoing=e.get('incoming_player_id'),e.get('outgoing_player_id');actor(incoming,True);actor(outgoing,True)
        add('participation_events',mid,index,e['type'],incoming,outgoing,e)
    for inn in envelope['innings']:
        number=_positive_integer(inn['innings_number']);clock=0;ordinal=0
        _boolean(inn['include_in_standard_stats']);_boolean(inn['super_over'])
        for over in inn['overs']:
            _quantity(over['over'])
            for d in over['deliveries']:
                ordinal+=1;actor(d['batter_id']);actor(d['bowler_id']);actor(d['non_striker_id'],True)
                _boolean(d['is_legal']);_positive_integer(d['ball_number'])
                _positive_integer(d.get('delivery_ordinal',ordinal))
                _quantity(d.get('legal_balls_before',clock));_quantity(d.get('legal_balls_after',clock+int(d['is_legal'])))
                _quantity(d.get('source_over_number'),True)
                if d.get('delivery_ordinal',ordinal)!=ordinal:raise ValueError('Delivery order mismatch')
                observed=d['runs'];oe=d['extras'];scored=d.get('statistical_runs',observed);se=d.get('statistical_extras',oe)
                quantities=[]
                for runs,extras in ((observed,oe),(scored,se)):
                    quantities.extend(_quantity(runs[k]) for k in ('batter','extras','total'))
                    quantities.extend(_quantity(extras.get(k,0)) for k in ('wides','noballs','byes','legbyes','penalty'))
                after=clock+int(d['is_legal'])
                if d.get('legal_balls_before',clock)!=clock or d.get('legal_balls_after',after)!=after:raise ValueError('Actual ball clock mismatch')
                charge=scored['batter']+se.get('wides',0)+se.get('noballs',0)
                add('deliveries',mid,number,ordinal,over['over'],d['ball_number'],d['batter_id'],d['bowler_id'],d['non_striker_id'],d['is_legal'],clock,after,*quantities,
                    charge,d['phase'],d.get('nominal_phase'),d.get('source_actual_delivery',d.get('actual_delivery')),d.get('source_unique_marker'),d.get('source_over_number'),d.get('tactical'),d.get('review'))
                clock=after
                for wi,w in enumerate(d['wickets']):
                    actor(w['player_out_id']);kind=w['kind']
                    if kind not in NON_DISMISSALS|BOWLER_DISMISSALS|OTHER_DISMISSALS:raise ValueError('Unrecognized dismissal')
                    out=int(kind not in NON_DISMISSALS);credit=int(kind in BOWLER_DISMISSALS)
                    add('dismissal_events',mid,number,f'd{ordinal}:w{wi}',ordinal,w['player_out_id'],kind,False,1,out,out,credit,w.get('fielders',[]),w)
        source_only=inn.get('source_only_timed_out_count')
        if source_only is not None:_quantity(source_only)
        if source_only:
            add('dismissal_events',mid,number,'source:timed_out',None,None,'timed out',True,source_only,source_only,None,0,[],{'source_scorecard':inn['source_scorecard'],'attribution':'unknown'})
        metadata={k:v for k,v in inn.items() if k!='overs'}
        add('innings',mid,number,inn['batting_team'],inn['bowling_team'],inn['include_in_standard_stats'],inn['super_over'],inn.get('innings_kind'),_quantity(inn['innings_penalty_runs']),clock,
            _quantity(inn['source_scorecard'].get('balls'),True),source_only,inn['source_scorecard'],metadata)
    add('source_documents',mid,gzip.compress(raw_bytes,compresslevel=6,mtime=0),hashlib.sha256(raw_bytes).hexdigest(),envelope['source_sha256'])
    return rows


def validate_subset(manifest_path:Path,payload_dir:Path)->dict:
    raw=manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SUBSET_SHA256:raise ValueError('Subset differs from independently pinned manifest')
    m=json.loads(raw)
    ids,held=m['record_ids'],m['held_ids']
    if m['version']!='d3_verified_test_subset_v1' or len(ids)!=len(set(ids)) or len(ids)!=m['verified_records'] or len(held)!=len(set(held)) or set(ids)&set(held):raise ValueError('Invalid subset identity ledger')
    if m['full_admitted_snapshot_ready'] is not False or m['db_api_page_parity_proven'] is not False:raise ValueError('Unexpected readiness authority')
    if set(m['payload_sha256'])!={'matches.jsonl','checks.jsonl','identity-crosswalk.json'}:raise ValueError('Unexpected payload manifest')
    for name,digest in m['payload_sha256'].items():
        path=payload_dir/name
        if path.is_symlink() or not path.is_file() or _sha(path)!=digest:raise ValueError(f'Payload hash mismatch:{name}')
    for name,digest in m['source_code_sha256'].items():
        if name not in ('ingestion/source_identity.py','ingestion/source_adapter.py','ingestion/source_checks.py','ingestion/source_attribution.py','scripts/verify_source_adapter.py') or _sha(ROOT/name)!=digest:raise ValueError('Verified converter code differs')
    if _sha(ROOT/'ingestion/attribution_evidence.json')!=m['attribution_evidence_sha256']:raise ValueError('Attribution evidence differs')
    return m


IMPORT_CODE_PATHS=('db/source_connection.py','db/source_schema.sql','ingestion/source_store.py')
LOADED_IMPORT_CODE={name:_sha(ROOT/name) for name in IMPORT_CODE_PATHS}


def copy_rows(session,rows):
    """Internal controlled/cohort writer;caller controls transaction,guard is mandatory."""
    import io
    assert_proof_session(session)
    with session.connection.cursor() as cur:
        for table,columns in COLUMNS.items():
            if not rows.get(table):continue
            text=''.join(csv_line(row) for row in rows[table])
            command=sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV,NULL '\\N')").format(sql.Identifier(SCHEMA),sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)))
            cur.copy_expert(command.as_string(cur),io.StringIO(text))


def import_subset(descriptor:Path,manifest_path:Path,payload_dir:Path,staging:Path,match_ids:list[str]|None=None)->dict:
    started=time.monotonic();manifest=validate_subset(manifest_path,payload_dir);allowed=set(manifest['record_ids'])
    requested=manifest['record_ids'] if match_ids is None else match_ids
    if not requested or len(requested)!=len(set(requested)) or not set(requested)<=allowed:raise ValueError('Not a unique verified subset')
    if staging.exists():raise FileExistsError('Staging must be fresh')
    staging.mkdir(mode=0o700);run_id=str(uuid.uuid4());counts={t:0 for t in COLUMNS};players={};aliases={};seen=set();consumed=hashlib.sha256()
    streams={t:(staging/f'{t}.csv').open('wb') for t in COLUMNS}
    generated={t:hashlib.sha256() for t in COLUMNS};byte_counts={t:0 for t in COLUMNS}
    def write(table,row):
        data=csv_line(row).encode('utf-8');streams[table].write(data);generated[table].update(data);byte_counts[table]+=len(data);counts[table]+=1
    try:
        with (payload_dir/'matches.jsonl').open('rb') as stream:
            for raw in stream:
                consumed.update(raw);envelope=json.loads(raw);mid=envelope['match_id']
                if mid in seen or mid not in allowed:raise ValueError('Duplicate/unverified payload ID')
                seen.add(mid)
                if mid not in requested:continue
                rows=match_rows(envelope,raw,run_id)
                for row in rows.pop('players'):
                    pid,public,name=row
                    if pid in players and players[pid][1]!=public:raise ValueError('Conflicting global player identity')
                    if pid not in players or players[pid][2] is None:players[pid]=row
                for row in rows.pop('player_aliases'):
                    key=row[:2]
                    if key in aliases and aliases[key]!=row:raise ValueError('Conflicting global source alias')
                    aliases[key]=row
                for table,data in rows.items():
                    for row in data:write(table,row)
        if seen!=allowed or consumed.hexdigest()!=manifest['payload_sha256']['matches.jsonl']:raise ValueError('Consumed bytes/IDs differ from verified payload')
        for row in players.values():write('players',row)
        for row in aliases.values():write('player_aliases',row)
        holds={};checks_consumed=hashlib.sha256()
        with (payload_dir/'checks.jsonl').open('rb') as stream:
            for line in stream:
                checks_consumed.update(line);result=json.loads(line)
                if result['errors']:holds[result['match_id']]=result['errors']
        if checks_consumed.hexdigest()!=manifest['payload_sha256']['checks.jsonl']:raise ValueError('Consumed check bytes differ from verified payload')
        if set(holds)!=set(manifest['held_ids']):raise ValueError('Hold ledger differs')
        for mid,reason in holds.items():write('source_holds',(mid,'extras_or_source_authority',reason,SUBSET_SHA256))
    finally:
        for stream in streams.values():stream.close()
    if any(_sha(ROOT/name)!=digest for name,digest in LOADED_IMPORT_CODE.items()):raise ValueError('Import code changed during staging')
    session=connect_proof(descriptor);committed=False
    try:
        apply_schema(session);conn=session.connection;mode='full' if match_ids is None else 'cohort'
        if f'_{mode}_' not in session.descriptor['database']:raise ValueError('Wrong cohort/full target')
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_d3_import'))")
            for table in COLUMNS:
                cur.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM {}.{})').format(sql.Identifier(SCHEMA),sql.Identifier(table)))
                if cur.fetchone()[0]:raise ValueError('Proof facts are nonempty;refuse overwrite')
            cur.execute(f'INSERT INTO {SCHEMA}.import_runs VALUES(%s,%s,%s,%s,%s,%s,%s,now())',(run_id,SUBSET_SHA256,mode,len(requested),manifest['held_records'],False,json.dumps(manifest)))
            for table,columns in COLUMNS.items():
                command=sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV,NULL '\\N')").format(sql.Identifier(SCHEMA),sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)))
                fd=os.open(staging/f'{table}.csv',os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Staging must remain a regular file')
                    consumed_rows=_DigestReader(stream);cur.copy_expert(command.as_string(cur),consumed_rows)
                    if consumed_rows.digest.hexdigest()!=generated[table].hexdigest() or consumed_rows.size!=byte_counts[table]:raise ValueError('Consumed staging bytes differ from generated projection')
            actual={}
            for table in COLUMNS:
                cur.execute(sql.SQL('SELECT COUNT(*) FROM {}.{}').format(sql.Identifier(SCHEMA),sql.Identifier(table)));actual[table]=cur.fetchone()[0]
            if actual!=counts:raise ValueError('Stored row counts differ from staging')
        assert_proof_session(session)
        if any(_sha(ROOT/name)!=digest for name,digest in LOADED_IMPORT_CODE.items()):raise ValueError('Import code changed before commit')
        receipt={'run_id':run_id,'mode':mode,'target':dict(session.descriptor),'manifest_sha256':SUBSET_SHA256,'expected_matches':len(requested),'stored_matches':counts['matches'],'table_counts':counts,'full_admitted_snapshot_ready':False,'storage_import_passed':True,'source_code_sha256':LOADED_IMPORT_CODE,'staging_sha256':{t:h.hexdigest() for t,h in generated.items()},'staging_bytes':byte_counts,'elapsed_seconds':round(time.monotonic()-started,3),'limits':['Storage/import proof only;complete API/page parity not proven','18 source holds remain excluded']}
        pending=staging/'import-receipt.pending.json'
        pending.write_text(json.dumps(receipt,indent=2)+'\n')
        with conn.cursor() as cur:
            cur.execute(f'UPDATE {SCHEMA}.import_runs SET provenance=%s WHERE run_id=%s',(json.dumps({'manifest':manifest,'receipt':receipt}),run_id))
        conn.commit();committed=True
        try:
            os.replace(pending,staging/'import-receipt.json')
            return {**receipt,'receipt_published':True}
        except OSError as error:
            # The database transaction and its receipt are durable. Export failure
            # must not be reported as a rolled-back import or invite a retry.
            return {**receipt,'receipt_published':False,'receipt_export_error':str(error)}
    except Exception:
        if not committed:session.connection.rollback()
        raise
    finally:session.close()
