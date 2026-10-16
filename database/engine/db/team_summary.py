"""Guarded final five materializations; never install into the ordinary database."""
import hashlib
from pathlib import Path
from db.player_summary import BASE,SCHEMA as PLAYER
from db.opposition_read import assert_opposition_read
from db.matchup_read import assert_matchup_read
from ingestion import source_store as source

SCHEMA='cricket_team_read_v1'
SCHEMA_PATH=Path(__file__).with_name('team_summary.sql')
INDEXES={'team_vs_team':['team_a','team_b','scope_key'],
 'team_vs_team_seasons':['team_a','team_b','scope_key','year','season_key'],
 'team_recent_matches':['team_a','team_b','scope_key','date','match_key'],
 'venue_stats':['venue_key','scope_key','year'],'stat_cards':['stat_id']}


def verify_score_projection(session):
    """All ordinary scorecards must equal independent delivery/event totals."""
    with session.connection.cursor() as c:
        c.execute(f'''SELECT COUNT(*) FROM {BASE}.innings i JOIN {BASE}.v_innings_totals v USING(match_id,innings_number)
          WHERE i.standard_stats AND (jsonb_typeof(i.source_scorecard->'runs') IS DISTINCT FROM 'number'
          OR jsonb_typeof(i.source_scorecard->'wickets') IS DISTINCT FROM 'number'
          OR (i.source_scorecard->>'runs')::numeric IS DISTINCT FROM v.team_runs
          OR (i.source_scorecard->>'wickets')::numeric IS DISTINCT FROM v.team_wickets
          OR i.actual_legal_balls IS DISTINCT FROM v.actual_legal_balls)''')
        if c.fetchone()[0]:raise ValueError('Source score projection differs from verified facts')


def _assert_team_read(session):
    a=assert_opposition_read(session);assert_matchup_read(session)
    with session.connection.cursor() as c:
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is None:raise ValueError('Team summaries are not installed')
        c.execute(f'SELECT ddl_sha256,source_run_id,manifest_sha256,codebook_sha256,cluster_run_id FROM {SCHEMA}.read_environment')
        if c.fetchall()!=[(source._sha(SCHEMA_PATH),a['run_id'],a['manifest_sha256'],a['codebook_sha256'],session.descriptor['run_id'])]:raise ValueError('Team read authority differs')
        c.execute('SELECT matviewname,ispopulated FROM pg_matviews WHERE schemaname=%s ORDER BY matviewname',(SCHEMA,))
        if c.fetchall()!=[(n,True) for n in sorted(INDEXES)]:raise ValueError('Team summary population differs')
        c.execute('''SELECT t.relname,idx.relname,i.indisunique,i.indisvalid,i.indisready,i.indnullsnotdistinct,i.indpred IS NULL,i.indexprs IS NULL,
          ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,pos) JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum ORDER BY k.pos)
          FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_class idx ON idx.oid=i.indexrelid
          JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relkind='m' ORDER BY t.relname,idx.relname''',(SCHEMA,))
        if c.fetchall()!=[(n,n+'_key',True,True,True,True,True,True,INDEXES[n]) for n in sorted(INDEXES)]:raise ValueError('Team summary unique index authority differs')
        c.execute(f'SELECT COUNT(*),COUNT(DISTINCT match_key) FROM {SCHEMA}.team_recent_matches')
        if c.fetchone()!=(a['matches'],a['matches']):raise ValueError('Team match coverage differs')
    return a


def assert_team_read(session):
    try:return _assert_team_read(session)
    except (KeyError,TypeError,AttributeError) as e:
        raise ValueError('Malformed private read authority') from e


def install_team_summaries(session):
    a=assert_opposition_read(session);assert_matchup_read(session)
    with session.connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_team_read_install'))")
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is not None:raise ValueError('Team schema nonempty; refuse overwrite')
        c.execute("SET LOCAL statement_timeout='120s';SET LOCAL jit=off")
        c.execute(f'SELECT COUNT(*) FROM(SELECT m.match_id FROM {BASE}.matches m LEFT JOIN {BASE}.match_teams t USING(match_id) GROUP BY m.match_id HAVING COUNT(t.team_name)<>2)x')
        if c.fetchone()[0]:raise ValueError('Exactly two source teams required')
        verify_score_projection(session)
        raw=SCHEMA_PATH.read_bytes();c.execute(raw.decode())
        c.execute(f'INSERT INTO {SCHEMA}.read_environment VALUES(true,%s,%s,%s,%s,%s)',
          (hashlib.sha256(raw).hexdigest(),a['run_id'],a['manifest_sha256'],a['codebook_sha256'],session.descriptor['run_id']))
    assert_team_read(session);return {'materialized_views':5,'source_run_id':a['run_id']}
