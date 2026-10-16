"""Independent compact SQL/document parity against authenticated source artifacts."""
import gzip
import hashlib
import json
from pathlib import Path
import time

from db.source_connection import connect_proof,validate_descriptor,assert_proof_session
from db.compact_connection import SCHEMA,SCHEMA_PATH,CODEC_PATH
from ingestion import source_store as source
from ingestion.compact_codec import Codebook,decode_block

# Native COPY baseline independently established before this compact importer.
# These constants are authoritative ONLY for the pinned 5,215-record subset.
FULL_SUBSET_SHA='50a20f4303c8803da3258dbce166842ac2bd9d4f7c4d33c06d931b0adfe72afe'
FULL_DELIVERY_SHA='5e78cc6525ad25ce10db7b1f749acb07d7d21b6a92b644c530a46135f98ce35d'
FULL_DELIVERY_BYTES=1189088529
FULL_RELATION_BASELINES={'actor_innings': {'sha256': 'b21724d3dac363127d03d1f6e63a8cfc04409553f53f695ea9bd5d2cf5ba2440', 'stream_bytes': 8131521}, 'dismissal_events': {'sha256': 'bb024147439428492ea7ad3bcbf09e3e49986eddb217dbad0664cacc51ffb73b', 'stream_bytes': 26018263}, 'innings': {'sha256': 'a4f11689c6ce5d25cae14b59d48bc0fce58faca5dec36884267f2431f584427e', 'stream_bytes': 8612016}, 'lineup_assertions': {'sha256': '1f93b9be2e9adbfddb47b89eeb45eb804892b6607f91208e15dbf317bc7fd8da', 'stream_bytes': 389072}, 'lineup_members': {'sha256': '0c1682595d211b5ec20843c75b34d2610168dd5195a37856c56026af01df67db', 'stream_bytes': 5177726}, 'match_teams': {'sha256': '6bdf57be1f0352fa90144ddbcfab147348e2d305a2f52f0f18a2dd77136ee044', 'stream_bytes': 252634}, 'participants': {'sha256': '812084d34d5ae76e6683e5e3ebf5a68c142053b0ad3050cdb5fc4028dd8b8970', 'stream_bytes': 62229041}, 'participation_events': {'sha256': '9368e7eefd4c18cc7341ff1741e9671faa6dfdad53678103edb720ff89b32074', 'stream_bytes': 114818}, 'player_aliases': {'sha256': 'a731d05de2c154d5da36cf0664c05efe107b5f01f82c8f5a01aada631eb7b9e7', 'stream_bytes': 104446}, 'players': {'sha256': '620935e7c4d4ae5132d178d680b1ead99c9af1c44f3a5ac2de86dbc11808d6ff', 'stream_bytes': 107977}, 'source_documents': {'sha256': '5fd010e3912a72ca10840e3a70d1791f7bdb0726e2c01e4afca6382a5737da2c', 'stream_bytes': 119569888}, 'source_holds': {'sha256': '28f333dbc9c73e4398debf101705f35bda33aa29cee882d4a1e11d1db90f3f73', 'stream_bytes': 5776}}


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


class _Sink:
    def __init__(self):self.digest=hashlib.sha256();self.bytes=0
    def write(self,data):self.digest.update(data);self.bytes+=len(data);return len(data)


def _native_digest(conn,statement):
    sink=_Sink()
    with conn.cursor() as c:c.copy_expert('COPY('+statement+') TO STDOUT WITH(FORMAT BINARY)',sink)
    return {'sha256':sink.digest.hexdigest(),'stream_bytes':sink.bytes}


def _expected(manifest,payload,selected,retain_projection=False):
    docs,matches,eligibility,participants={},{},{},{}
    projections={table:[] for table in source.COLUMNS} if retain_projection else None
    players,aliases={},{}
    digest=hashlib.sha256();seen=set()
    with (payload/'matches.jsonl').open('rb') as stream:
        for raw in stream:
            digest.update(raw); e=json.loads(raw);mid=e['match_id']
            if mid in seen or mid not in manifest['record_ids']:raise ValueError('Invalid source IDs')
            seen.add(mid)
            if mid not in selected:continue
            if retain_projection:
                rows=source.match_rows(e,raw,'independent-cohort')
                for row in rows.pop('players'):
                    pid,public,name=row
                    if pid not in players or players[pid][2] is None:players[pid]=row
                for row in rows.pop('player_aliases'):aliases[row[:2]]=row
                for table,values in rows.items():projections[table].extend(values)
            docs[mid]=(hashlib.sha256(raw).hexdigest(),e['source_sha256'])
            m=e['meta'];dates=m.get('dates') or []
            matches[(mid,)]=(dates[0] if dates else None,str(m['season']) if m.get('season') is not None else None,
                m['format_bucket'],m['record_class'],m['playing_format'],m['balls_per_over'],
                m.get('competition'),m.get('source_competition_trophy_id'),m.get('venue'),
                m.get('venue_country'),m.get('city'),m.get('gender'),e['outcome'],e['toss'],m)
            for pid,p in e['players'].items():participants[(mid,pid)]=(p.get('name'),p.get('participant_roles',[]),p.get('team_ids',[]),p)
            for inn in e['innings']:
                eligibility[(mid,inn['innings_number'])]=(inn['include_in_standard_stats'],inn['super_over'],
                    inn.get('innings_kind'),inn['batting_team'],inn['bowling_team'])
    if seen!=set(manifest['record_ids']) or digest.hexdigest()!=manifest['payload_sha256']['matches.jsonl'] or set(docs)!=selected:
        raise ValueError('Consumed source authority differs')
    counters={name:{} for name in ('innings','batters','bowlers','phases')};holds={};seen=set();digest=hashlib.sha256()
    with (payload/'checks.jsonl').open('rb') as stream:
        for raw in stream:
            digest.update(raw);check=json.loads(raw);mid=check['match_id']
            if mid in seen:raise ValueError('Duplicate check IDs')
            seen.add(mid)
            if check['errors']:holds[(mid,)]=('extras_or_source_authority',check['errors'],source.SUBSET_SHA256)
            if mid not in selected:continue
            if check['errors']:raise ValueError('Held record in selected source')
            for inn in check['innings']:
                key=(mid,inn['innings_number'])
                counters['innings'][key]=(inn['team_runs'],inn['actual_legal_balls'],inn['team_wickets'],
                    inn['delivered_balls'],inn['innings_penalty_runs'],inn['unknown_non_striker_deliveries'])
                for pid,b in inn['batters'].items():counters['batters'][key+(pid,)]=(b['runs'],b['balls_faced'],b['dismissals'])
                for pid,b in inn['bowlers'].items():counters['bowlers'][key+(pid,)]=(b['runs_conceded'],b['actual_legal_balls'],b['wickets'])
                for phase,p in inn['phase_counts'].items():counters['phases'][key+(phase,)]=(p['deliveries'],p['legal_balls'],p['runs'])
    if digest.hexdigest()!=manifest['payload_sha256']['checks.jsonl'] or seen!=set(manifest['record_ids'])|set(manifest['held_ids']) or {k[0] for k in holds}!=set(manifest['held_ids']):
        raise ValueError('Consumed check/hold authority differs')
    if retain_projection:
        projections['players']=list(players.values());projections['player_aliases']=list(aliases.values())
        projections['source_holds']=[(key[0],*values) for key,values in holds.items()]
    return docs,matches,eligibility,participants,counters,holds,projections


def _stored_codebook(conn):
    with conn.cursor() as c:
        c.execute(f'SELECT player_id,key FROM {SCHEMA}.player_map');players=dict(c.fetchall())
        c.execute(f'SELECT match_id,key FROM {SCHEMA}.match_map');matches=dict(c.fetchall())
        c.execute(f'SELECT name,id,value FROM {SCHEMA}.dictionary ORDER BY name,id');dictionaries={}
        for name,key,text in c.fetchall():dictionaries.setdefault(name,{})[key]=text
    book=Codebook(players,dictionaries)
    raw=_json({'players':players,'matches':matches,'dictionaries':{n:dict(v) for n,v in book.dictionaries.items()},'codec_version':1}).encode()
    return book,matches,hashlib.sha256(raw).hexdigest()


def verify_compact_database(descriptor:Path,manifest_path:Path,payload_dir:Path,
                            report_path:Path,match_ids:list[str]|None=None)->dict:
    validate_descriptor(descriptor)
    if report_path.exists() or report_path.is_symlink():raise FileExistsError('Proof report must be fresh')
    started=time.monotonic();manifest=source.validate_subset(manifest_path,payload_dir)
    ids=manifest['record_ids'] if match_ids is None else match_ids
    if type(ids) is not list or not ids or len(ids)!=len(set(ids)) or not set(ids)<=set(manifest['record_ids']):raise ValueError('Invalid verified selection')
    selected=set(ids); mode='full' if match_ids is None else 'cohort'
    docs,matches,eligibility,participants,counters,holds,projections=_expected(manifest,payload_dir,selected,retain_projection=match_ids is not None)
    session=connect_proof(descriptor);errors=[]
    try:
        conn=session.connection;conn.rollback()
        if match_ids is not None:
            # PostgreSQL forbids CTAS in a readonly transaction even for TEMP.
            # Prepare only a session-local reference table before the readonly
            # permanent-data snapshot; its later COPY touches no stored facts.
            with conn.cursor() as c:
                for table,columns in source.COLUMNS.items():
                    if table=='matches':continue
                    c.execute(f'CREATE TEMP TABLE compact_expected_{table} AS SELECT '+','.join(columns)+f' FROM {SCHEMA}.{table} WITH NO DATA')
            conn.commit()
        conn.set_session(readonly=True,isolation_level='REPEATABLE READ')
        with conn.cursor() as c:
            c.execute("SET jit=off;SET work_mem='64MB';SET statement_timeout='600s'")
            c.execute(f'SELECT schema_sha256,codec_sha256,cluster_run_id FROM {SCHEMA}.proof_environment')
            if c.fetchall()!=[(source._sha(SCHEMA_PATH),source._sha(CODEC_PATH),session.descriptor['run_id'])]:raise ValueError('Compact schema/codec/cluster authority differs')
            c.execute(f'SELECT manifest_sha256,mode,expected_matches,held_count,full_admitted_snapshot_ready,provenance FROM {SCHEMA}.import_runs')
            runs=c.fetchall()
            if len(runs)!=1 or runs[0][:5]!=(source.SUBSET_SHA256,mode,len(selected),manifest['held_records'],False):raise ValueError('Import authority differs')
            receipt=runs[0][5]['receipt']
            from ingestion.compact_store import LOADED_CODE
            if receipt.get('source_code_sha256')!=LOADED_CODE or any(source._sha(source.ROOT/name)!=sha for name,sha in LOADED_CODE.items()):
                raise ValueError('Compact receipt implementation binding differs')
            if receipt['manifest_sha256']!=source.SUBSET_SHA256 or receipt['target']!=dict(session.descriptor):raise ValueError('Durable receipt authority differs')
            c.execute(f'SELECT match_id FROM {SCHEMA}.matches')
            if {r[0] for r in c.fetchall()}!=selected:raise ValueError('Stored IDs differ')
        book,match_map,digest=_stored_codebook(conn)
        if digest!=receipt['codebook_sha256'] or set(match_map)!=selected:raise ValueError('Stored codebook digest differs')
        comparisons=[('matches',1,'match_id,date::text,season,format_bucket,record_class,playing_format,balls_per_group,competition,trophy_id,venue,venue_country,city,gender,outcome,toss,meta',matches),
            ('innings',2,'match_id,innings_number,standard_stats,super_over,innings_kind,batting_team,bowling_team',eligibility),
            ('participants',2,'match_id,player_id,source_name,roles,team_source_ids,source_facts',participants),
            ('source_holds',1,'match_id,stage,reason,manifest_sha256',holds),
            ('v_innings_totals',2,'match_id,innings_number,team_runs,actual_legal_balls,team_wickets,delivered_balls,penalty_runs,unknown_non_strikers',counters['innings']),
            ('v_batter_totals',3,'match_id,innings_number,player_id,runs,balls_faced,dismissals',counters['batters']),
            ('v_bowler_totals',3,'match_id,innings_number,player_id,runs_conceded,actual_legal_balls,wickets',counters['bowlers']),
            ('v_phase_totals',3,'match_id,innings_number,phase,delivered_balls,actual_legal_balls,runs',counters['phases'])]
        counts={}
        with conn.cursor() as c:
            for table,width,columns,expected in comparisons:
                c.execute(f'SELECT {columns} FROM {SCHEMA}.{table}');actual={}
                for row in c.fetchall():
                    key=tuple(row[:width])
                    if key in actual:raise ValueError('Duplicate SQL logical key')
                    actual[key]=tuple(row[width:])
                if set(actual)!=set(expected) or any(_json(actual.get(k))!=_json(v) for k,v in expected.items()):
                    errors.append({'relation':table,'error':'Source fields/counters or coverage differ'})
                counts[table]=len(actual)
        from ingestion.compact_store import validate_sql_counters
        validate_sql_counters(conn)
        document_count=0
        with conn.cursor(name='compact_source_documents') as c:
            c.itersize=32;c.execute(f'SELECT match_id,payload_gzip,payload_sha256,source_sha256 FROM {SCHEMA}.source_documents ORDER BY match_id')
            for mid,packed,payload_sha,source_sha in c:
                try:raw=gzip.decompress(bytes(packed))
                except (OSError,EOFError) as error:raise ValueError('Corrupt original source document') from error
                sha=hashlib.sha256(raw).hexdigest()
                if docs.get(mid)!=(sha,source_sha) or payload_sha!=sha:errors.append({'match_id':mid,'error':'Source bytes/hash differ'})
                document_count+=1
        if document_count!=len(selected):errors.append({'error':'Original document coverage differs'})
        with conn.cursor() as c:
            c.execute(f'SELECT COUNT(*),COALESCE(SUM(records),0),COUNT(*) FILTER(WHERE payload_sha256<>sha256(payload)) FROM {SCHEMA}.blocks')
            blocks,deliveries,bad_hashes=c.fetchone()
        if bad_hashes:raise ValueError('Stored block digest differs')
        native=None
        if match_ids is None:
            if source.SUBSET_SHA256!=FULL_SUBSET_SHA or len(selected)!=5215 or deliveries!=2686884:raise ValueError('Full native baseline is not valid for this authority')
            columns=','.join(source.COLUMNS['deliveries'])
            native=_native_digest(conn,f'SELECT {columns} FROM {SCHEMA}.deliveries ORDER BY match_id,innings_number,ordinal')
            if native!={'sha256':FULL_DELIVERY_SHA,'stream_bytes':FULL_DELIVERY_BYTES}:errors.append({'error':'Full PostgreSQL-native delivery bytes differ'})
            from ingestion.compact_store import TABLE_COLUMNS
            for table,expected_native in FULL_RELATION_BASELINES.items():
                names=source.COLUMNS.get(table,TABLE_COLUMNS.get(table))
                columns=','.join('"'+n+'"' for n in names)
                order=','.join('"'+n+'"::text COLLATE "C" NULLS FIRST' for n in names)
                actual_native=_native_digest(conn,f'SELECT {columns} FROM {SCHEMA}.{table} ORDER BY {order}')
                if actual_native!=expected_native:errors.append({'relation':table,'error':'Full original native relation bytes differ'})
        else:
            # Independent source projection in SQL's native types for the small
            # cohort; neither the packed encoder nor decoder creates this reference.
            import io
            for table,expected_rows in projections.items():
                if table=='matches':continue
                with conn.cursor() as c:
                    c.copy_expert(f"COPY compact_expected_{table} FROM STDIN WITH(FORMAT CSV,NULL '\\N')",io.StringIO(''.join(source.csv_line(row) for row in expected_rows)))
                names=source.COLUMNS[table];columns=','.join(names)
                order=','.join('"'+n+'"::text COLLATE "C" NULLS FIRST' for n in names)
                actual_native=_native_digest(conn,f'SELECT {columns} FROM {SCHEMA}.{table} ORDER BY {order}')
                if actual_native!=_native_digest(conn,f'SELECT {columns} FROM compact_expected_{table} ORDER BY {order}'):
                    errors.append({'relation':table,'error':'Cohort original native field bytes differ'})
                if table=='deliveries':native=actual_native
        with conn.cursor() as c:c.execute('SELECT pg_database_size(current_database())');size=c.fetchone()[0]
        assert_proof_session(session)
        result={'storage_and_counter_parity_passed':not errors,'target':dict(session.descriptor),'mode':mode,
            'matches':len(selected),'documents':document_count,'held_count':len(holds),'deliveries':deliveries,'blocks':blocks,
            'codebook_digest_verified':True,'block_payload_hashes_verified':True,'native_delivery_stream':native,
            'sql_match_dimensions_checked':counts['matches'],'sql_eligibility_rows_checked':counts['innings'],
            'sql_participant_rows_checked':counts['participants'],'sql_innings_checked':counts['v_innings_totals'],
            'sql_batter_rows_checked':counts['v_batter_totals'],'sql_bowler_rows_checked':counts['v_bowler_totals'],
            'sql_phase_rows_checked':counts['v_phase_totals'],'actor_cache_independently_verified':True,
            'full_admitted_snapshot_ready':False,'database_bytes_in_current_session':size,
            'elapsed_seconds':round(time.monotonic()-started,3),'errors':errors,
            'limits':['Fresh compact base schema; final ten consumer materializations not included','No hosted/update/correction/API/Stat Builder proof','Cohort measurement can include session temporary reference table; fresh size measured separately']}
        with report_path.open('x') as stream:stream.write(json.dumps(result,indent=2)+'\n')
        if errors:raise ValueError('Compact parity failed; see '+str(report_path))
        return result
    finally:session.connection.rollback();session.close()
