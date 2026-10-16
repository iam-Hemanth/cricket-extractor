#!/usr/bin/env python3
"""Independent source-based reconciliation of the last five summary families."""
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,json,sys,time
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from db.source_connection import connect_proof,assert_proof_session
from db.team_summary import assert_team_read,verify_score_projection,SCHEMA,BASE,PLAYER
from db.team_read import read_team_h2h,read_team_dashboard,read_venue,read_stat_cards,_card_payloads,COUNTS
from db.player_read import PlayerFilters,_query


def classify_outcome(outcome,a,b):
    winner=outcome.get('winner');result=outcome.get('result');eliminator=outcome.get('eliminator')
    if result is None and eliminator is None and winner in(a,b):return 1 if winner==a else 2
    if result=='tie':
        if eliminator in(a,b) and winner in(None,eliminator):return 7 if eliminator==a else 8
        if eliminator is None and winner is None:return 3
    if winner is None and eliminator is None:
        if result=='draw':return 4
        if result=='no result':return 5
    return 6


def result_counts(rows):
    codes=Counter(m['code'] for m in rows)
    return (len(rows),codes[1]+codes[7],codes[2]+codes[8],codes[3],codes[4],codes[5],codes[6],codes[7]+codes[8])


def verify_team_summaries(session):
    a=assert_team_read(session);start=time.monotonic()
    with session.connection.cursor() as c:c.execute("SET statement_timeout='120s';SET jit=off;SET work_mem='64MB'")
    verify_score_projection(session)
    scopes={r['key']:r for r in _query(session,f'SELECT * FROM {PLAYER}.scopes')}
    scopekeys={(r['record_class'],r['playing_format'],r['balls_per_group'],r['competition_key']):k for k,r in scopes.items()}
    teams={r['key']:r['name'] for r in _query(session,'SELECT * FROM cricket_opposition_read_v1.teams')};teamkeys={v:k for k,v in teams.items()}
    venues={r['key']:(r['venue'],r['city'],r['country']) for r in _query(session,f'SELECT * FROM {SCHEMA}.venues')};venuekeys={v:k for k,v in venues.items()}
    seasonkeys={r['season']:r['key'] for r in _query(session,f'SELECT * FROM {SCHEMA}.seasons')}
    pairnames=defaultdict(list)
    for r in _query(session,f'SELECT * FROM {BASE}.match_teams'):pairnames[r['match_id']].append(r['team_name'])
    matches=[]
    for m in _query(session,f'SELECT m.*,mp.key AS match_key FROM {BASE}.matches m JOIN {BASE}.match_map mp USING(match_id)'):
        names=sorted(pairnames[m['match_id']],key=lambda n:teamkeys[n])
        if len(names)!=2:raise ValueError('Independent pair cardinality differs')
        comp='trophy:'+str(m['trophy_id']) if m['trophy_id'] is not None else 'raw:'+m['competition'] if m['competition'] is not None else 'none'
        m.update(team_a=teamkeys[names[0]],team_b=teamkeys[names[1]],code=classify_outcome(m['outcome'],*names),year=m['date'].year if m['date'] else None,
          scope_key=scopekeys[(m['record_class'],m['playing_format'],m['balls_per_group'],comp)],venue_key=venuekeys[(m['venue'],m['city'],m['venue_country'])],season_key=seasonkeys[m['season']])
        matches.append(m)
    bykey={m['match_key']:m for m in matches}
    innings=_query(session,f'SELECT * FROM {BASE}.innings WHERE standard_stats')
    innmap=defaultdict(list)
    innings_by_identity={(i['match_key'],i['innings_number']):i for i in innings}
    for i in innings:i.update(team_runs=i['source_scorecard']['runs'],team_wickets=i['source_scorecard']['wickets']);innmap[i['match_key']].append(i)
    grouped=defaultdict(list);annual=defaultdict(list);grounds=defaultdict(list)
    for m in matches:
        pair=(m['team_a'],m['team_b'],m['scope_key']);grouped[pair].append(m)
        annual[(*pair,m['year'],m['season_key'])].append(m);grounds[(m['venue_key'],m['scope_key'],m['year'])].append(m)
    def venue_expected(items):
        r=dict.fromkeys(('matches','decisive_matches','ties','draws','no_results','unclassified','standard_innings','first_score_sum','first_score_count','second_score_sum','second_score_count','chase_decided_matches','chase_wins'),0)
        high=[];low=[]
        for m in items:
            r['matches']+=1;code=m['code'];r['decisive_matches']+=code in(1,2,7,8)
            for name,c in [('ties',3),('draws',4),('no_results',5),('unclassified',6)]:r[name]+=code==c
            observed=innmap[m['match_key']];r['standard_innings']+=len(observed);number={i['innings_number']:i for i in observed}
            for n,name in [(1,'first'),(2,'second')]:
                if n in number:r[name+'_score_sum']+=number[n]['team_runs'];r[name+'_score_count']+=1
            high.extend(i['team_runs'] for i in observed);low.extend(i['team_runs'] for i in observed if i['team_wickets']==10)
            if m['playing_format'] in('one_day','t20','hundred_ball') and code in(1,2) and m['outcome'].get('method') is None and 1 in number and 2 in number and number[1]['batting_team']!=number[2]['batting_team']:
                r['chase_decided_matches']+=1;r['chase_wins']+=m['outcome'].get('winner')==number[2]['batting_team']
        r['highest_team_total']=max(high,default=None);r['lowest_ten_wicket_total']=min(low,default=None)
        return r
    venuecols=('matches','decisive_matches','ties','draws','no_results','unclassified','standard_innings','first_score_sum','first_score_count',
      'second_score_sum','second_score_count','highest_team_total','lowest_ten_wicket_total','chase_decided_matches','chase_wins')
    expected={'team_vs_team':([*('team_a','team_b','scope_key'),*COUNTS],[(*k,*result_counts(v)) for k,v in grouped.items()]),
      'team_vs_team_seasons':(['team_a','team_b','scope_key','year','season_key',*COUNTS],[(*k,*result_counts(v)) for k,v in annual.items()]),
      'team_recent_matches':(['team_a','team_b','scope_key','date','match_key','year','result_code'],[(m['team_a'],m['team_b'],m['scope_key'],m['date'],m['match_key'],m['year'],m['code']) for m in matches]),
      'venue_stats':(['venue_key','scope_key','year',*venuecols],[(*k,*(venue_expected(v)[col] for col in venuecols)) for k,v in grounds.items()])}
    summaries={}
    with session.connection.cursor() as c:
        for name,(columns,rows) in expected.items():
            c.execute(f'SELECT {",".join(columns)} FROM {SCHEMA}.{name}');actual=c.fetchall()
            if Counter(rows)!=Counter(actual):raise ValueError('Independent all-row mismatch: '+name)
            c.execute('SELECT pg_total_relation_size(%s::regclass)',(SCHEMA+'.'+name,))
            summaries[name]={'rows':len(rows),'all_values_equal':True,'indexed_bytes':c.fetchone()[0],
              'expected_sha256':hashlib.sha256(json.dumps(sorted(rows,key=repr),default=str).encode()).hexdigest()}
    actors=_query(session,f'SELECT a.*,mp.match_id FROM {BASE}.actor_innings a JOIN {BASE}.innings i USING(match_key,innings_number) JOIN {BASE}.match_map mp ON mp.key=a.match_key WHERE i.standard_stats')
    sixes=_query(session,f'''SELECT f.match_key,f.batter AS player_key,COUNT(*) AS value FROM {BASE}.facts f
      JOIN {BASE}.innings i USING(match_key,innings_number) JOIN {BASE}.matches m USING(match_id)
      WHERE i.standard_stats AND m.playing_format='t20' AND m.record_class IN('t20','t20i') AND f.scored_batter=6 AND f.observed_wides=0 GROUP BY f.match_key,f.batter''')
    def selected_matches(filters):
        return [m for m in matches if all(value is None or m[col]==value for col,value in [('record_class',filters.record_class),('playing_format',filters.playing_format),('trophy_id',filters.competition_id),('year',filters.year)])]
    def card_expected(filters):
        selected=selected_matches(filters);keys={m['match_key'] for m in selected}
        if not selected:return {}
        values=Counter()
        for row in sixes:
            if row['match_key'] in keys:values[row['player_key']]+=row['value']
        six=None
        if values:
            player=min(values,key=lambda p:(-values[p],p));six={'player_key':player,'value':values[player]}
        observed=[i for i in innings if i['match_key'] in keys];high=None
        if observed:
            i=min(observed,key=lambda i:(-i['team_runs'],i['match_key'],i['innings_number']));high={'team_key':teamkeys[i['batting_team']],'value':i['team_runs'],'match_key':i['match_key'],'innings_number':i['innings_number'],'scope_key':bykey[i['match_key']]['scope_key']}
        selectedactors=[r for r in actors if r['match_key'] in keys];bowling=[r for r in selectedactors if r['role']==2];best=None
        if bowling:
            r=min(bowling,key=lambda r:(-r['dismissals'],r['runs'],bykey[r['match_key']]['date'] is None,-int(bykey[r['match_key']]['date'].strftime('%Y%m%d')) if bykey[r['match_key']]['date'] else 0,r['match_id'],r['innings_number'],r['player_key']))
            best={'player_key':r['player_key'],'team_key':teamkeys[innings_by_identity[(r['match_key'],r['innings_number'])]['bowling_team']],'wickets':r['dismissals'],'runs_conceded':r['runs'],'match_key':r['match_key'],'innings_number':r['innings_number'],'scope_key':bykey[r['match_key']]['scope_key']}
        activity=defaultdict(set)
        for r in selectedactors:activity[r['player_key']].add(r['match_key'])
        most=None
        if activity:
            p=min(activity,key=lambda p:(-len(activity[p]),p));most={'player_key':p,'value':len(activity[p])}
        return {'most_t20_sixes':six,'highest_total':high,'best_figures':best,'most_matches':most}
    real=[]
    pair=min(grouped,key=lambda k:(-len(grouped[k]),k));first,second=teams[pair[0]],teams[pair[1]]
    year=Counter(m['year'] for m in grouped[pair] if m['year'] is not None).most_common(1)[0][0]
    for filters in (PlayerFilters(),PlayerFilters(record_class=scopes[pair[2]]['record_class'],year=year),PlayerFilters(competition_id=117)):
        expectedcards=card_expected(filters);actualcards={r['stat_id']:r['payload'] for r in _card_payloads(session,filters)}
        if actualcards!=expectedcards:raise ValueError('Independent cards mismatch')
        real.append({'family':'cards','filters':filters.__dict__,'source_equal':True,'nonempty_payloads':sum(v is not None for v in actualcards.values())})
    summaries['stat_cards']={'rows':4,'all_values_equal':True}
    for filters in (PlayerFilters(),PlayerFilters(record_class=scopes[pair[2]]['record_class'],year=year)):
        before=time.monotonic();r=read_team_h2h(session,first,second,filters)
        selected=[m for m in selected_matches(filters) if(m['team_a'],m['team_b'])==pair[:2]]
        if not selected or not r['leaders']['batting'] or not r['recent']:raise ValueError('Positive H2H control absent')
        counts=result_counts(selected)
        if r['results']!=dict(zip(('matches','first_wins','second_wins','ties','draws','no_results','unclassified','decided_ties'),counts)):raise ValueError('Real H2H results differ')
        recent=sorted(selected,key=lambda m:(m['date'] is not None,m['date'].isoformat() if m['date'] else '',m['match_id']),reverse=True)[:15]
        if [m['match_id'] for m in recent]!=[m['match_id'] for m in r['recent']]:raise ValueError('Recent real ordering/filter differs')
        for name in ('by_scope','by_year','seasons'):
            if sum(v['matches'] for v in r[name])!=len(selected):raise ValueError('H2H breakdown differs')
        real.append({'family':'h2h','first':first,'second':second,'filters':filters.__dict__,'matches':len(selected),'warm_local_ms':round((time.monotonic()-before)*1000,3),'source_equal':True})
    venue=min(venues,key=lambda k:(-sum(len(v) for g,v in grounds.items() if g[0]==k),k))
    for filters in (PlayerFilters(),PlayerFilters(year=max(m['year'] for m in matches if m['venue_key']==venue and m['year'] is not None))):
        selected=[m for m in selected_matches(filters) if m['venue_key']==venue];v=read_venue(session,venue,filters);e=venue_expected(selected)
        if not selected or any(v['totals'][col]!=value for col,value in e.items()):raise ValueError('Positive venue source control differs')
        real.append({'family':'venue','venue_key':venue,'filters':filters.__dict__,'matches':len(selected),'source_equal':True})
    dashboard=read_team_dashboard(session,first,PlayerFilters());selected=[m for m in matches if first in pairnames[m['match_id']]]
    if dashboard['results']['matches']!=len(selected):raise ValueError('Dashboard metadata coverage differs')
    noinnings=[m for m in matches if not innmap[m['match_key']]]
    assert_proof_session(session)
    return {'all_five_summary_families_equal_source':True,'matches':len(matches),'standard_innings':len(innings),
      'no_innings_matches_preserved':[m['match_id'] for m in noinnings],'result_distribution':dict(Counter(m['code'] for m in matches)),
      'summaries':summaries,'real_reads':real,'elapsed_seconds':round(time.monotonic()-start,3),
      'source_run_id':a['run_id'],'held_matches':a['held_count'],'full_admitted_snapshot_ready':a['ready'],
      'limits':['Raw identities; no canonical crosswalk','Local steady proof only; refresh/corrections/growth/hosting separate','Detailed dashboard slices and complete scorecard/Stat Builder remain']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--descriptor',required=True,type=Path);p.add_argument('--report',required=True,type=Path);args=p.parse_args()
    if args.report.exists():raise FileExistsError('Fresh report required')
    s=connect_proof(args.descriptor)
    try:s.connection.rollback();s.connection.set_session(readonly=True,isolation_level='REPEATABLE READ');result=verify_team_summaries(s)
    finally:s.connection.rollback();s.close()
    s=connect_proof(args.descriptor)
    try:
        with s.connection.cursor() as c:
            c.execute('SELECT pg_database_size(current_database())');result['database_bytes']=c.fetchone()[0]
            c.execute("SELECT schemaname,matviewname,pg_total_relation_size((schemaname||'.'||matviewname)::regclass) FROM pg_matviews WHERE schemaname LIKE 'cricket_%' ORDER BY schemaname,matviewname");result['all_materialized_sizes']=c.fetchall()
        if len(result['all_materialized_sizes'])!=10:raise ValueError('Exactly ten final materializations required')
    finally:s.close()
    with args.report.open('x') as f:f.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
