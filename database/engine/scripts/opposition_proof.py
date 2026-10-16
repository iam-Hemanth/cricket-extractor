#!/usr/bin/env python3
"""Private all-row reconciliation and real directional/opposition read proof."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from db.source_connection import connect_proof,assert_proof_session
from db.player_read import PlayerFilters,_query
from db.opposition_read import assert_opposition_read,read_player_vs_team
from db.matchup_read import assert_matchup_read,read_matchup,COUNTERS


def validate_matchup_control(result, *, require_activity):
    """Reject vacuous positive proofs and reconcile the returned detail."""
    if require_activity and (not result['totals']['balls_faced'] or not result['rows'] or not result['recent_deliveries']):
        raise ValueError('Expected positive real pair control')
    for col in COUNTERS:
        if sum(v[col] for v in result['venues'])!=result['totals'][col]:
            raise ValueError('Pair detail/summary reconciliation differs')
    if sum(v['bowler_wickets'] for v in result['dismissals'])!=result['totals']['dismissals']:
        raise ValueError('Pair dismissal credits differ')
    recent=result['recent_deliveries']
    if len(recent)>10:raise ValueError('Recent pair window too large')
    filters=result.get('filters',{})
    for row in [*result['rows'],*recent]:
        for name in ('record_class','playing_format','competition_id','phase'):
            if filters.get(name) is not None and row[name]!=filters[name]:
                raise ValueError('Pair selector membership differs')
        year=row.get('year') if 'year' in row else (int(str(row['date'])[:4]) if row['date'] else None)
        if filters.get('year') is not None and year!=filters['year']:
            raise ValueError('Pair year membership differs')
    order=lambda row:(row['date'] is not None,str(row['date'] or ''),row['match_id'],row['innings_number'],row['ordinal'])
    if recent!=sorted(recent,key=order,reverse=True):raise ValueError('Pair recent ordering differs')


def verify_opposition(session):
    assert_opposition_read(session);assert_matchup_read(session);conn=session.connection;start=time.monotonic()
    with conn.cursor() as c:c.execute("SET statement_timeout='120s';SET jit=off;SET work_mem='64MB'")
    # Direct source actor totals, independent of coarse helper and its view.
    pvt='''SELECT a.role,a.player_key,t.key AS opposition_key,COUNT(DISTINCT a.match_key)::smallint,
        COUNT(*)::smallint,SUM(a.runs)::smallint,SUM(a.balls)::smallint,SUM(a.dismissals)::smallint
        FROM cricket_compact_v1.actor_innings a JOIN cricket_compact_v1.innings i USING(match_key,innings_number)
        JOIN cricket_opposition_read_v1.teams t ON t.name=CASE WHEN a.role=1 THEN i.bowling_team ELSE i.batting_team END
        WHERE i.standard_stats GROUP BY a.role,a.player_key,t.key'''
    # Map scopes through source matches independently of the pair cached rows.
    pair='''WITH facts AS MATERIALIZED(SELECT f.*,ms.scope_key,ms.year FROM cricket_compact_v1.facts f
        JOIN cricket_compact_v1.innings i USING(match_key,innings_number)
        JOIN cricket_player_read_v1.match_scope ms USING(match_key) WHERE i.standard_stats),
        face AS(SELECT batter,bowler,scope_key,year,phase,SUM(scored_batter)::bigint AS runs,
        COUNT(*) FILTER(WHERE observed_wides=0) AS balls,COUNT(*) FILTER(WHERE observed_wides=0 AND observed_noballs=0) AS legal,
        COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=0) AS zeroes,
        COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=4) AS fours,COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=6) AS sixes
        FROM facts GROUP BY batter,bowler,scope_key,year,phase),
        outs AS(SELECT p.key AS batter,f.bowler,f.scope_key,f.year,f.phase,SUM(e.bowler_wickets)::bigint AS wickets
        FROM cricket_compact_v1.dismissal_events e JOIN cricket_compact_v1.match_map m USING(match_id)
        JOIN facts f ON f.match_key=m.key AND f.innings_number=e.innings_number AND f.ordinal=e.delivery_ordinal
        JOIN cricket_compact_v1.player_map p ON p.player_id=e.player_out_id WHERE NOT e.source_only GROUP BY p.key,f.bowler,f.scope_key,f.year,f.phase)
        SELECT COALESCE(a.batter,o.batter)::smallint,COALESCE(a.bowler,o.bowler)::smallint,COALESCE(a.scope_key,o.scope_key),COALESCE(a.year,o.year)::smallint,COALESCE(a.phase,o.phase)::smallint,
        COALESCE(a.runs,0)::smallint,COALESCE(a.balls,0)::smallint,COALESCE(a.legal,0)::smallint,COALESCE(a.zeroes,0)::smallint,
        COALESCE(a.fours,0)::smallint,COALESCE(a.sixes,0)::smallint,COALESCE(o.wickets,0)::smallint FROM face a FULL JOIN outs o ON a.batter=o.batter AND a.bowler=o.bowler AND a.scope_key=o.scope_key AND a.year IS NOT DISTINCT FROM o.year AND a.phase=o.phase'''
    summaries={}
    with conn.cursor() as c:
        for relation,expected in [('cricket_opposition_read_v1.player_vs_team',pvt),('cricket_matchup_read_v1.batter_vs_bowler',pair)]:
            c.execute(f'WITH expected AS({expected}),missing AS(SELECT * FROM expected EXCEPT ALL SELECT * FROM {relation}),extra AS(SELECT * FROM {relation} EXCEPT ALL SELECT * FROM expected) SELECT (SELECT COUNT(*) FROM missing)+(SELECT COUNT(*) FROM extra)')
            if c.fetchone()[0]:raise ValueError('All-row source reconciliation differs: '+relation)
            c.execute(f'SELECT COUNT(*),pg_total_relation_size(%s::regclass) FROM {relation}',(relation,));rows,size=c.fetchone()
            summaries[relation]={'rows':rows,'indexed_bytes':size,'all_values_equal':True}
    real=[]
    with conn.cursor() as c:
        c.execute("""SELECT b.player_id,w.player_id,v.year FROM cricket_matchup_read_v1.batter_vs_bowler v
          JOIN cricket_player_read_v1.scopes s ON s.key=v.scope_key
          JOIN cricket_compact_v1.player_map b ON b.key=v.batter_key
          JOIN cricket_compact_v1.player_map w ON w.key=v.bowler_key
          WHERE s.competition_id=117 AND v.year IS NOT NULL AND EXISTS(
            SELECT 1 FROM cricket_compact_v1.player_aliases a WHERE a.player_id=b.player_id AND a.source='cricsheet')
          GROUP BY b.player_id,w.player_id,v.year HAVING SUM(v.balls_faced)>0 AND SUM(v.dismissals)>0
          ORDER BY SUM(v.balls_faced) DESC,b.player_id,w.player_id,v.year LIMIT 1""")
        chosen=c.fetchone()
    if not chosen:raise ValueError('Positive filtered real pair absent')
    batter_id,bowler_id,year=chosen
    controls=[('career','espn_253802','espn_43590',PlayerFilters(),True),
      ('competition',batter_id,bowler_id,PlayerFilters(competition_id=117),True),
      ('competition_year',batter_id,bowler_id,PlayerFilters(year=year,competition_id=117),True),
      ('expected_empty','espn_253802','espn_43590',PlayerFilters(year=2025),False)]
    for label,batter,bowler,selected,positive in controls:
        before=time.monotonic();r=read_matchup(session,batter,bowler,selected)
        validate_matchup_control(r,require_activity=positive)
        if not positive and (any(r['totals'][col] for col in COUNTERS) or r['recent_deliveries'] or r['rows']):
            raise ValueError('Expected empty pair control differs')
        real.append({'family':'matchup','control':label,'batter_id':batter,'bowler_id':bowler,'filters':r['filters'],
          'positive_activity_required':positive,'exact_detail_summary_equal':True,'warm_local_ms':round((time.monotonic()-before)*1000,3),
          'runs':r['totals']['runs'],'balls':r['totals']['balls_faced'],'dismissals':r['totals']['dismissals'],'recent_count':len(r['recent_deliveries'])})
    for pid,mode in [('espn_253802','batting'),('espn_43590','bowling')]:
        role=1 if mode=='batting' else 2
        with conn.cursor() as c:
            c.execute("""SELECT t.name,a.year FROM cricket_opposition_read_v1.actor_opposition a JOIN cricket_opposition_read_v1.teams t ON t.key=a.opposition_key
              JOIN cricket_compact_v1.player_map p ON p.key=a.player_key WHERE a.role=%s AND p.player_id=%s AND a.standard_stats AND a.year IS NOT NULL
              GROUP BY t.name,a.year HAVING SUM(a.balls)>0 ORDER BY SUM(a.balls) DESC,t.name,a.year LIMIT 1""",(role,pid));team,year=c.fetchone()
        for selected in (PlayerFilters(),PlayerFilters(year=year)):
            before=time.monotonic();r=read_player_vs_team(session,pid,team,mode,selected)
            columns=('runs','balls_faced','dismissals') if role==1 else ('runs_conceded','legal_balls','wickets')
            if not r['totals']['observed_innings'] or not r['recent']:raise ValueError('Expected positive opposition control')
            for col in columns:
                if sum(v[col] for v in r['phases'])!=r['totals'][col] or sum(v[col] for v in r['venues'])!=r['totals'][col]:raise ValueError('Opposition detail/core totals differ')
            if selected.year is not None and any(v['year']!=selected.year for v in r['by_year']):raise ValueError('Opposition year membership differs')
            real.append({'family':'opposition','player_id':pid,'team':team,'filters':r['filters'],'mode':mode,'positive_activity_required':True,
              'detail_core_equal':True,'warm_local_ms':round((time.monotonic()-before)*1000,3),'observed_innings':r['totals']['observed_innings'],'recent_count':len(r['recent'])})
    with conn.cursor() as c:
        c.execute("SELECT source_id FROM cricket_compact_v1.player_aliases WHERE player_id=%s AND source='cricsheet' ORDER BY source_id LIMIT 1",(batter_id,));alias=c.fetchone()
    if not alias:raise ValueError('Real alias coverage absent')
    selected=PlayerFilters(year=chosen[2],competition_id=117)
    canonical=read_matchup(session,batter_id,bowler_id,selected)
    aliased=read_matchup(session,alias[0],bowler_id,selected)
    validate_matchup_control(aliased,require_activity=True)
    if aliased!=canonical:raise ValueError('Real matchup alias mismatch')
    assert_proof_session(session)
    return {'all_summary_rows_equal':True,'summaries':summaries,'real_reads':real,'real_alias_equal':True,'elapsed_seconds':round(time.monotonic()-start,3),
        'limits':['Five of ten families; remaining team/venue/cards/fullSB/operations budget unproved','Raw team identities; canonical aliases/maidens evidence separate','Local warm only, no hosted/cold/concurrent guarantee']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--descriptor',type=Path,required=True);p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    if args.report.exists():raise FileExistsError('Fresh report required')
    s=connect_proof(args.descriptor)
    try:s.connection.rollback();s.connection.set_session(readonly=True,isolation_level='REPEATABLE READ');r=verify_opposition(s)
    finally:s.connection.rollback();s.close()
    s=connect_proof(args.descriptor)
    with s.connection.cursor() as c:c.execute('SELECT pg_database_size(current_database())');r['database_bytes']=c.fetchone()[0]
    s.close()
    with args.report.open('x') as f:f.write(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))


if __name__=='__main__':main()
