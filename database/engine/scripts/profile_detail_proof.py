#!/usr/bin/env python3
"""Read-only private profile/partnership verification; no source replay."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from db.source_connection import connect_proof,assert_proof_session
from db.player_read import PlayerFilters,read_player
from db.profile_detail import read_profile_detail
from db.partnership_read import SCHEMA,SCHEMA_PATH,assert_partnership_read,read_partnerships
from ingestion.partnerships import segment_partnerships
from db.player_read import _query


def verify_profile_detail(session):
    assert_partnership_read(session);conn=session.connection;start=time.monotonic()
    with conn.cursor() as c:c.execute("SET statement_timeout='60s';SET jit=off;SET work_mem='64MB'")
    # Whole-family runs/ball coverage is reconciled to independent decoded facts.
    with conn.cursor() as c:
        c.execute(f'SELECT SUM(total_runs),SUM(delivered_balls),SUM(legal_balls) FROM {SCHEMA}.partnerships');actual=c.fetchone()
        c.execute('''SELECT SUM(f.scored_batter+f.scored_wides+f.scored_noballs+f.scored_byes+f.scored_legbyes+f.scored_penalties),COUNT(*),COUNT(*) FILTER(WHERE f.observed_wides=0 AND f.observed_noballs=0)
            FROM cricket_compact_v1.facts f JOIN cricket_compact_v1.innings i USING(match_key,innings_number)
            WHERE i.standard_stats AND f.non_striker>0 AND f.non_striker<>f.batter''');expected=c.fetchone()
        if actual!=expected:raise ValueError('Whole partnership run/ball coverage differs')
        c.execute(f'SELECT COUNT(*),pg_total_relation_size(%s::regclass) FROM {SCHEMA}.partnerships',(SCHEMA+'.partnerships',));rows,size=c.fetchone()
        c.execute("SELECT match_id,key FROM cricket_compact_v1.match_map WHERE match_id IN('1252666','913657','1359475','1000851','1384429','1030217') ORDER BY match_id");matches=c.fetchall()
    # SQL's raw stint relation and a separate Python event segmentation agree on
    # representative real innings. The stored MV itself only contains aggregates.
    if {mid for mid,_ in matches}!={'1252666','913657','1359475','1000851','1384429','1030217'}:
        raise ValueError('Required representative match coverage is missing')
    keys=[key for _,key in matches]
    facts=_query(session,'SELECT * FROM cricket_compact_v1.facts WHERE match_key=ANY(%s) ORDER BY match_key,innings_number,ordinal',(keys,))
    events=_query(session,'''SELECT mp.key AS match_key,e.innings_number,e.delivery_ordinal,p.key AS player_out_key,e.kind,e.team_wickets,e.source_only
        FROM cricket_compact_v1.dismissal_events e JOIN cricket_compact_v1.match_map mp USING(match_id)
        LEFT JOIN cricket_compact_v1.player_map p ON p.player_id=e.player_out_id WHERE mp.key=ANY(%s)''',(keys,))
    eligible={(r['match_key'],r['innings_number']) for r in _query(session,'SELECT match_key,innings_number FROM cricket_compact_v1.innings WHERE standard_stats AND match_key=ANY(%s)',(keys,))}
    pure=segment_partnerships([f for f in facts if (f['match_key'],f['innings_number']) in eligible],events)
    text=SCHEMA_PATH.read_text().split('CREATE MATERIALIZED VIEW partnerships AS\n',1)[1]
    raw=text.split('), pair_innings AS',1)[0]+')'
    raw=raw.replace('WHERE i.standard_stats\n','WHERE i.standard_stats AND f.match_key=ANY(%s)\n')
    with conn.cursor() as c:
        c.execute(raw+' SELECT match_key,innings_number,pair1,pair2,runs,delivered_balls,legal_balls FROM stints',(keys,));sql=c.fetchall()
    projected=[(s['match_key'],s['innings_number'],s['pair1'],s['pair2'],s['total_runs'],s['delivered_balls'],s['legal_balls']) for s in pure]
    if not pure or not sql:raise ValueError('Real representative stint coverage is empty')
    if sorted(sql)!=sorted(projected):raise ValueError('Independent real stint segmentation differs')
    profiles=[]
    for pid in ('espn_253802','espn_43590'):
        for selected in (PlayerFilters(),PlayerFilters(year=2025),PlayerFilters(competition_id=117)):
            before=time.monotonic();detail=read_profile_detail(session,pid,selected);core=read_player(session,pid,selected)
            for role,columns in [('batting',('runs','balls_faced','dismissals')),('bowling',('runs_conceded','legal_balls','wickets'))]:
                for col in columns:
                    if sum(r[col] for r in detail['phases'][role])!=core[role]['totals'][col] or sum(r[col] for r in detail['venues'][role])!=core[role]['totals'][col]:raise ValueError('Profile phase/venue totals differ')
                if len(detail['recent'][role])>10:raise ValueError('Recent window exceeds contract')
            if detail['participation']['recorded_appearance_lower_bound']+detail['participation']['unresolved_listed_matches']!=detail['participation']['listed_matches']:raise ValueError('Participation coverage arithmetic differs')
            profiles.append({'player_id':pid,'filters':detail['filters'],'phase_venue_core_equal':True,'warm_local_read_ms':round((time.monotonic()-before)*1000,3),'participation':detail['participation']})
    partnerships=[]
    for pid in ('espn_253802','espn_43590'):
        before=time.monotonic();answer=read_partnerships(session,pid,PlayerFilters())
        partnerships.append({'player_id':pid,'qualified_rows':len(answer['rows']),'warm_local_read_ms':round((time.monotonic()-before)*1000,3),'coverage':answer['coverage']})
    with conn.cursor() as c:
        c.execute("SELECT source_id,player_id FROM cricket_compact_v1.player_aliases WHERE source='cricsheet' AND player_id='espn_253802' LIMIT 1");alias=c.fetchone()
    if alias and read_profile_detail(session,alias[0],PlayerFilters(year=2025))!=read_profile_detail(session,alias[1],PlayerFilters(year=2025)):raise ValueError('Detail alias mismatch')
    assert_proof_session(session)
    return {'whole_partnership_run_ball_coverage_equal':True,'partnership_rows':rows,'partnership_indexed_bytes':size,
        'real_segmentation_matches':len(matches),'real_stints_compared':len(pure),'sql_python_segmentation_equal':True,
        'profiles':profiles,'partnership_reads':partnerships,'real_alias_equal':bool(alias),'elapsed_seconds':round(time.monotonic()-start,3),
        'limits':['Three of ten summary families; remaining seven and fullStatBuilder/operations not proved','Observed participation lowerbound, award/home evidence unavailable','Local warm only; no cold/concurrent/hosted proof']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--descriptor',type=Path,required=True);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    if args.report.exists():raise FileExistsError('Fresh report required')
    s=connect_proof(args.descriptor)
    try:
        s.connection.rollback();s.connection.set_session(readonly=True,isolation_level='REPEATABLE READ');result=verify_profile_detail(s)
    finally:s.connection.rollback();s.close()
    s=connect_proof(args.descriptor)
    with s.connection.cursor() as c:c.execute('SELECT pg_database_size(current_database())');size=c.fetchone()[0]
    s.close();result['database_bytes']=size
    with args.report.open('x') as f:f.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
