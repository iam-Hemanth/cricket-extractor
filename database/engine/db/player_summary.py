"""Guarded installation of the first two compact consumer summary families."""
import hashlib
import json
from pathlib import Path

from db.source_connection import assert_proof_session
from db.compact_connection import SCHEMA as BASE,SCHEMA_PATH as BASE_PATH,CODEC_PATH
from ingestion.compact_store import LOADED_CODE
from ingestion import source_store as source

SCHEMA='cricket_player_read_v1'
SCHEMA_PATH=Path(__file__).with_name('player_summary.sql')


def base_authority(session):
    from db.runtime_connection import RuntimeSession,assert_runtime_session,verify_runtime_dataset
    runtime=isinstance(session,RuntimeSession)
    (assert_runtime_session if runtime else assert_proof_session)(session)
    with session.connection.cursor() as c:
        c.execute(f'SELECT schema_sha256,codec_sha256,cluster_run_id FROM {BASE}.proof_environment')
        expected=(source._sha(BASE_PATH),source._sha(CODEC_PATH),session.descriptor['run_id'])
        if c.fetchall()!=[expected]:raise ValueError('Base schema/codec authority differs')
        c.execute(f'SELECT run_id,manifest_sha256,expected_matches,held_count,full_admitted_snapshot_ready,provenance FROM {BASE}.import_runs')
        rows=c.fetchall()
        if len(rows)!=1:raise ValueError('Single authenticated base import required')
        run_id,manifest,matches,holds,ready,provenance=rows[0];receipt=provenance['receipt']
        if receipt.get('source_code_sha256')!=LOADED_CODE or any(source._sha(source.ROOT/n)!=sha for n,sha in LOADED_CODE.items()):raise ValueError('Base implementation authority differs')
        if receipt.get('target')!=dict(session.descriptor) or receipt.get('manifest_sha256')!=manifest:raise ValueError('Base receipt target/manifest differs')
        c.execute(f'SELECT COUNT(*) FROM {BASE}.matches')
        if c.fetchone()[0]!=matches:raise ValueError('Base match coverage differs')
    result={'run_id':run_id,'manifest_sha256':manifest,'matches':matches,'held_count':holds,'ready':ready,'codebook_sha256':receipt['codebook_sha256']}
    if runtime:verify_runtime_dataset(session,result)
    return result


def assert_player_read(session):
    authority=base_authority(session)
    with session.connection.cursor() as c:
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is None:raise ValueError('Player summaries are not installed')
        c.execute(f'SELECT ddl_sha256,source_run_id,manifest_sha256,codebook_sha256,cluster_run_id FROM {SCHEMA}.read_environment')
        if c.fetchall()!=[(source._sha(SCHEMA_PATH),authority['run_id'],authority['manifest_sha256'],authority['codebook_sha256'],session.descriptor['run_id'])]:raise ValueError('Player read authority differs')
        c.execute("SELECT matviewname,ispopulated FROM pg_matviews WHERE schemaname=%s ORDER BY matviewname",(SCHEMA,))
        if c.fetchall()!=[('player_batting',True),('player_bowling',True)]:raise ValueError('Player summaries are not populated')
        c.execute('''SELECT t.relname,idx.relname,i.indisunique,i.indisvalid,i.indisready,i.indnullsnotdistinct,
          ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,pos) JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum ORDER BY k.pos)
          FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace
          WHERE n.nspname=%s AND t.relkind='m' ORDER BY t.relname,idx.relname''',(SCHEMA,))
        expected=[(name,name+'_key',True,True,True,True,['player_key','scope_key','year']) for name in ('player_batting','player_bowling')]
        if c.fetchall()!=expected:raise ValueError('Player summary unique index authority differs')
        c.execute(f'SELECT COUNT(*),COUNT(DISTINCT match_id) FROM {SCHEMA}.match_scope')
        if c.fetchone()!=(authority['matches'],authority['matches']):raise ValueError('Scope mapping coverage differs')
    return authority


def install_player_summaries(session):
    assert_proof_session(session)
    authority=base_authority(session)
    # Authenticate the actually persisted codebook before deriving summaries.
    from db.compact_verify import _stored_codebook
    _,_,digest=_stored_codebook(session.connection)
    if digest!=authority['codebook_sha256']:raise ValueError('Base codebook differs')
    with session.connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_player_read_install'))")
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is not None:raise ValueError('Read schema is nonempty; refuse overwrite')
        c.execute("SET LOCAL statement_timeout='60s';SET LOCAL jit=off")
        raw=SCHEMA_PATH.read_bytes();c.execute(raw.decode())
        c.execute(f'INSERT INTO {SCHEMA}.read_environment VALUES(true,%s,%s,%s,%s,%s)',
            (hashlib.sha256(raw).hexdigest(),authority['run_id'],authority['manifest_sha256'],authority['codebook_sha256'],session.descriptor['run_id']))
    assert_player_read(session)
    return {'materialized_views':2,'source_run_id':authority['run_id'],'scope':'private standard player core only'}
