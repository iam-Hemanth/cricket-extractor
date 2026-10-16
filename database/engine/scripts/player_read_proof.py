#!/usr/bin/env python3
"""Explicit private install and independent full player-core proof."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from db.source_connection import connect_proof,assert_proof_session
from db.player_summary import SCHEMA,BASE,install_player_summaries,assert_player_read
from db.player_read import PlayerFilters,read_player,compare_players


DIRECT=f'''SELECT a.*,m.match_id,m.date,EXTRACT(YEAR FROM m.date)::integer AS year,s.key AS scope_key
 FROM {BASE}.actor_innings a JOIN {BASE}.innings i USING(match_key,innings_number)
 JOIN {BASE}.match_map mp ON mp.key=a.match_key JOIN {BASE}.matches m ON m.match_id=mp.match_id
 JOIN {SCHEMA}.scopes s ON s.record_class=m.record_class AND s.playing_format=m.playing_format
 AND s.balls_per_group=m.balls_per_group AND s.competition_key=CASE WHEN m.trophy_id IS NOT NULL THEN 'trophy:'||m.trophy_id::text WHEN m.competition IS NOT NULL THEN 'raw:'||m.competition ELSE 'none' END
 WHERE i.standard_stats'''


def verify_player_summaries(session):
    assert_player_read(session);conn=session.connection;started=time.monotonic()
    bat=f'''WITH direct AS({DIRECT}) SELECT player_key,scope_key,year,COUNT(DISTINCT match_key)::integer,
      COUNT(*)::integer,SUM(runs)::integer,SUM(balls)::integer,SUM(dismissals)::integer,
      COUNT(*) FILTER(WHERE runs BETWEEN 50 AND 99)::integer,COUNT(*) FILTER(WHERE runs>=100)::integer,
      COUNT(*) FILTER(WHERE runs=0 AND dismissals>0)::integer,MAX(runs),MAX(runs) FILTER(WHERE dismissals=0)
      FROM direct WHERE role=1 GROUP BY player_key,scope_key,year'''
    bowl=f'''WITH direct AS({DIRECT}),ranked AS(SELECT *,ROW_NUMBER() OVER(PARTITION BY player_key,scope_key,year ORDER BY dismissals DESC,runs,date DESC NULLS LAST,match_id,innings_number) AS rank FROM direct WHERE role=2),
      match_wickets AS(SELECT player_key,scope_key,year,match_key,SUM(dismissals) AS wickets FROM ranked GROUP BY player_key,scope_key,year,match_key),
      ten AS(SELECT player_key,scope_key,year,COUNT(*) FILTER(WHERE wickets>=10)::integer AS hauls FROM match_wickets GROUP BY player_key,scope_key,year),
      grouped AS(SELECT player_key,scope_key,year,COUNT(*)::integer AS innings,SUM(runs)::integer AS runs,SUM(balls)::integer AS balls,SUM(dismissals)::integer AS wickets,COUNT(*) FILTER(WHERE dismissals>=5)::integer AS five,
      MAX(dismissals) FILTER(WHERE rank=1) AS best_wickets,MAX(runs) FILTER(WHERE rank=1) AS best_runs,MAX(balls) FILTER(WHERE rank=1) AS best_balls,MAX(match_key) FILTER(WHERE rank=1) AS best_match,MAX(innings_number) FILTER(WHERE rank=1) AS best_innings FROM ranked GROUP BY player_key,scope_key,year)
      SELECT g.player_key,g.scope_key,g.year,g.innings,g.runs,g.balls,g.wickets,g.five,t.hauls,g.best_wickets,g.best_runs,g.best_balls,g.best_match,g.best_innings FROM grouped g JOIN ten t ON(t.player_key=g.player_key AND t.scope_key=g.scope_key AND t.year IS NOT DISTINCT FROM g.year)'''
    contents={}
    with conn.cursor() as c:
        for table,expected in [('player_batting',bat),('player_bowling',bowl)]:
            c.execute(f'WITH expected AS({expected}),missing AS(SELECT * FROM expected EXCEPT ALL SELECT * FROM {SCHEMA}.{table}),extra AS(SELECT * FROM {SCHEMA}.{table} EXCEPT ALL SELECT * FROM expected) SELECT (SELECT COUNT(*) FROM missing)+(SELECT COUNT(*) FROM extra)')
            if c.fetchone()[0]:raise ValueError('Independent full summary answer differs: '+table)
            c.execute(f'SELECT COUNT(*),pg_total_relation_size(%s::regclass) FROM {SCHEMA}.{table}',(SCHEMA+'.'+table,))
            count,size=c.fetchone();contents[table]={'rows':count,'indexed_bytes':size,'all_values_equal':True}
    checks=[]
    with conn.cursor() as c:
        c.execute(f'SELECT p.player_id FROM {BASE}.players p WHERE p.espn_id IN(253802,43590) ORDER BY p.espn_id');people=[r[0] for r in c.fetchall()]
        if len(people)<2:
            c.execute(f'SELECT player_id FROM {BASE}.players ORDER BY player_id LIMIT 2');people=[r[0] for r in c.fetchall()]
    for pid in people:
        for filters in (PlayerFilters(),PlayerFilters(year=2025),PlayerFilters(competition_id=117),PlayerFilters(playing_format='hundred_ball')):
            with conn.cursor() as c:
                c.execute(f'SELECT EXISTS(SELECT 1 FROM {SCHEMA}.scopes WHERE playing_format=%s)',('hundred_ball',))
                if filters.playing_format and not c.fetchone()[0]:continue
            before=time.monotonic();answer=read_player(session,pid,filters);elapsed=(time.monotonic()-before)*1000
            values=(pid,filters.record_class,filters.record_class,filters.playing_format,filters.playing_format,filters.competition_id,filters.competition_id,filters.year,filters.year)
            with conn.cursor() as c:
                c.execute(f'''SELECT a.role,SUM(a.runs),SUM(a.balls),SUM(a.dismissals),COUNT(*),COUNT(DISTINCT a.match_key)
                  FROM {BASE}.actor_innings a JOIN {BASE}.player_map p ON p.key=a.player_key JOIN {BASE}.innings i USING(match_key,innings_number)
                  JOIN {BASE}.match_map mp ON mp.key=a.match_key JOIN {BASE}.matches m ON m.match_id=mp.match_id
                  WHERE p.player_id=%s AND i.standard_stats AND (%s IS NULL OR m.record_class=%s) AND (%s IS NULL OR m.playing_format=%s)
                  AND (%s IS NULL OR m.trophy_id=%s) AND (%s IS NULL OR EXTRACT(YEAR FROM m.date)::integer=%s) GROUP BY a.role''',values)
                expected={r[0]:r[1:] for r in c.fetchall()}
            for role,kind,cols in [(1,'batting',('runs','balls_faced','dismissals')),(2,'bowling',('runs_conceded','legal_balls','wickets'))]:
                observed=answer[kind]['totals'];actual=tuple(observed[col] for col in (*cols,'observed_innings','observed_matches'))
                if actual!=expected.get(role,(0,0,0,0,0)):raise ValueError('Independent filtered player totals differ')
            checks.append({'player_id':pid,'filters':answer['filters'],'exact_counters_equal':True,'warm_local_read_ms':round(elapsed,3)})
    comparison=compare_players(session,*people,PlayerFilters(competition_id=117))
    if comparison['first']['filters']!=comparison['second']['filters']:raise ValueError('Comparison filters differ')
    with conn.cursor() as c:
        c.execute(f'SELECT source_id,player_id FROM {BASE}.player_aliases WHERE source=\'cricsheet\' ORDER BY source_id LIMIT 1')
        alias=c.fetchone()
    alias_verified=False
    if alias:
        if read_player(session,alias[0],PlayerFilters())!=read_player(session,alias[1],PlayerFilters()):raise ValueError('Real canonical/alias answers differ')
        alias_verified=True
    assert_proof_session(session)
    return {'all_summary_rows_exact':True,'summaries':contents,'representative_reads':checks,'comparison_same_filters':True,'real_alias_equivalence_verified':alias_verified,'elapsed_seconds':round(time.monotonic()-started,3),'limits':['Two of ten stored families; core profiles/comparison only','Local warm reads; hosted/cold/concurrent and complete detail/StatBuilder unproved']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['install','verify'])
    parser.add_argument('--descriptor',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():raise FileExistsError('Fresh report required')
    s=connect_proof(args.descriptor)
    try:
        if args.command=='install':
            result=install_player_summaries(s);s.connection.commit()
        else:
            s.connection.rollback();s.connection.set_session(readonly=True,isolation_level='REPEATABLE READ')
            result=verify_player_summaries(s)
    finally:s.connection.rollback();s.close()
    s=connect_proof(args.descriptor)
    with s.connection.cursor() as c:c.execute('SELECT pg_database_size(current_database())');size=c.fetchone()[0]
    s.close();result.update(database_bytes=size,target_descriptor=str(args.descriptor))
    with args.report.open('x') as out:out.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
