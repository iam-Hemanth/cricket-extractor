"""Guarded third summary installation and exact observed partnership reads."""
from dataclasses import asdict
import hashlib
from pathlib import Path

from db.source_connection import ProofSession
from db.player_summary import BASE,SCHEMA as PLAYER_SCHEMA,assert_player_read
from db.player_read import PlayerFilters,_query,_resolve,_validate_filters,_where
from ingestion import source_store as source

SCHEMA='cricket_partnership_read_v1'
SCHEMA_PATH=Path(__file__).with_name('partnership_summary.sql')


def assert_partnership_read(session):
    authority=assert_player_read(session)
    with session.connection.cursor() as c:
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is None:raise ValueError('Partnership summaries are not installed')
        c.execute(f'SELECT ddl_sha256,source_run_id,manifest_sha256,codebook_sha256,cluster_run_id FROM {SCHEMA}.read_environment')
        expected=(source._sha(SCHEMA_PATH),authority['run_id'],authority['manifest_sha256'],authority['codebook_sha256'],session.descriptor['run_id'])
        if c.fetchall()!=[expected]:raise ValueError('Partnership read authority differs')
        c.execute('SELECT matviewname,ispopulated FROM pg_matviews WHERE schemaname=%s ORDER BY matviewname',(SCHEMA,))
        if c.fetchall()!=[('partnerships',True)]:raise ValueError('Partnership summary is not populated')
        c.execute('''SELECT idx.relname,i.indisunique,i.indisvalid,i.indisready,i.indnullsnotdistinct,
            ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,pos)
            JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum ORDER BY k.pos),
            i.indpred IS NULL,i.indexprs IS NULL,am.amname,i.indnkeyatts,i.indnatts
            FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_am am ON am.oid=idx.relam
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=%s AND t.relkind='m' ORDER BY idx.relname''',(SCHEMA,))
        expected=[('partnerships_key',True,True,True,True,['pair1','pair2','scope_key','year'],True,True,'btree',4,4),
                  ('partnerships_reverse',False,True,True,False,['pair2','pair1','scope_key','year'],True,True,'btree',4,4)]
        if c.fetchall()!=expected:raise ValueError('Partnership index authority differs')
    return authority


def install_partnership_summaries(session:ProofSession)->dict:
    authority=assert_player_read(session)
    from db.compact_verify import _stored_codebook
    _,_,digest=_stored_codebook(session.connection)
    if digest!=authority['codebook_sha256']:raise ValueError('Base codebook differs')
    with session.connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_partnership_read_install'))")
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is not None:raise ValueError('Partnership schema is nonempty; refuse overwrite')
        c.execute("SET LOCAL statement_timeout='120s';SET LOCAL jit=off")
        raw=SCHEMA_PATH.read_bytes();c.execute(raw.decode())
        c.execute(f'INSERT INTO {SCHEMA}.read_environment VALUES(true,%s,%s,%s,%s,%s)',
            (hashlib.sha256(raw).hexdigest(),authority['run_id'],authority['manifest_sha256'],authority['codebook_sha256'],session.descriptor['run_id']))
    assert_partnership_read(session)
    return {'materialized_views':1,'source_run_id':authority['run_id'],'scope':'observed standard partnership stints'}


def read_partnerships(session:ProofSession,player_id:str,filters:PlayerFilters)->dict:
    authority=assert_partnership_read(session);_validate_filters(session,filters)
    player=_resolve(session,player_id);pid=player['player_id']
    key=_query(session,f'SELECT key FROM {BASE}.player_map WHERE player_id=%s',(pid,))[0]['key']
    where,params=_where(filters)
    rows=_query(session,f'''WITH selected AS (
        SELECT v.*,CASE WHEN v.pair1=%s THEN v.pair2 ELSE v.pair1 END AS partner_key
        FROM {SCHEMA}.partnerships v JOIN {PLAYER_SCHEMA}.scopes s ON s.key=v.scope_key
        WHERE (v.pair1=%s OR v.pair2=%s) AND {where}
    ), totals AS (
        SELECT partner_key,SUM(observed_stints)::bigint AS observed_stints,
        SUM(observed_pair_innings)::bigint AS observed_pair_innings,SUM(total_runs)::bigint AS total_runs,
        MAX(best_stint_runs) AS best_stint_runs,MAX(best_pair_innings_runs) AS best_pair_innings_runs,
        SUM(delivered_balls)::bigint AS delivered_balls,SUM(legal_balls)::bigint AS legal_balls,
        ROUND(SUM(total_runs)::numeric/NULLIF(SUM(observed_stints),0),2) AS mean_runs_per_observed_stint,
        ROUND(SUM(total_runs)::numeric/NULLIF(SUM(observed_pair_innings),0),2) AS mean_runs_per_pair_innings
        FROM selected GROUP BY partner_key HAVING SUM(observed_pair_innings)>=3
    ) SELECT t.*,p.player_id AS partner_id,p.name AS partner_name FROM totals t
        JOIN {BASE}.player_map pm ON pm.key=t.partner_key JOIN {BASE}.players p USING(player_id)
        ORDER BY t.total_runs DESC,t.observed_pair_innings DESC,p.player_id''',(key,key,key,*params))
    coverage_where,coverage_params=_where(filters,year='ms.year')
    coverage=_query(session,f'''WITH candidates AS (
        SELECT ms.match_key,i.innings_number,i.penalty_runs FROM {BASE}.participant_records pr
        JOIN {PLAYER_SCHEMA}.match_scope ms USING(match_id) JOIN {PLAYER_SCHEMA}.scopes s ON s.key=ms.scope_key
        JOIN {BASE}.innings i USING(match_id) WHERE pr.player_id=%s AND i.standard_stats AND {coverage_where}
    ), facts AS MATERIALIZED(
        SELECT c.match_key,c.innings_number,f.batter,f.non_striker FROM candidates c JOIN {BASE}.blocks b USING(match_key,innings_number)
        CROSS JOIN LATERAL {BASE}.decode_records(b.payload,b.records::integer,b.start_ordinal::integer) f
    ), present AS(SELECT DISTINCT match_key,innings_number FROM facts WHERE batter=%s OR non_striker=%s),
    selected AS(SELECT c.* FROM candidates c WHERE EXISTS(SELECT 1 FROM present p WHERE p.match_key=c.match_key AND p.innings_number=c.innings_number)
        OR EXISTS(SELECT 1 FROM {BASE}.actor_innings a WHERE a.role=1 AND a.player_key=%s AND a.match_key=c.match_key AND a.innings_number=c.innings_number))
    SELECT (SELECT COUNT(*) FROM facts WHERE (batter=%s OR non_striker=%s) AND(non_striker=0 OR batter=non_striker)) AS missing_or_invalid_partner_deliveries,
      (SELECT COALESCE(SUM(penalty_runs),0)::bigint FROM selected) AS unallocated_innings_penalty_runs''',
        (pid,*coverage_params,key,key,key,key,key))[0]
    coverage.update(dataset_matches=authority['matches'],held_matches=authority['held_count'],
        full_admitted_snapshot_ready=authority['ready'],eligibility='source_standard_stats',
        coverage_basis='observed_pair_presence_or_player_batting_innings',minimum_observed_pair_innings=3,
        run_basis='scored_deliveries_with_two_distinct_known_actors',
        means_basis='observed_stints_and_pair_innings')
    return {**player,'filters':asdict(filters),'source_run_id':authority['run_id'],
        'coverage':coverage,'availability':'qualified_observed_pairs' if rows else 'no_qualified_observed_pairs','rows':rows}
