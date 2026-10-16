"""Exact raw-team results, observed venue conditions and scoped dataset highlights."""
from collections import defaultdict
from dataclasses import asdict
from db.team_summary import SCHEMA,BASE,PLAYER,assert_team_read
from db.player_read import PlayerFilters,InvalidPlayerFilters,_validate_filters,_where,_query
from db.opposition_read import _totals as actor_totals

COUNTS=('matches','a_wins','b_wins','ties','draws','no_results','unclassified','decided_ties')
RESULT_NAMES={1:'win',2:'win',3:'tie',4:'draw',5:'no_result',6:'unclassified',7:'decider_win',8:'decider_win'}
CHASE_BASIS='ordinary_decisive_one_day_t20_hundred_with_two_ordered_innings_and_no_method'


def _team(session,name):
    if type(name) is not str or not name:raise InvalidPlayerFilters('Exact raw team name required')
    rows=_query(session,'SELECT key,name FROM cricket_opposition_read_v1.teams WHERE name=%s',(name,))
    if not rows:raise InvalidPlayerFilters('Unknown exact raw team')
    return rows[0]['key']


def _coverage(authority):
    return {'dataset_matches':authority['matches'],'held_matches':authority['held_count'],
      'full_admitted_snapshot_ready':authority['ready'],'team_identity':'exact_raw_source_name',
      'venue_identity':'exact_raw_venue_city_country','canonical_grouping':'unavailable_without_verified_crosswalk',
      'innings_eligibility':'source_standard_stats','economy_basis_balls':6}


def _matches(session,key,other,filters):
    where,params=_where(filters)
    pair='v.team_a=%s AND v.team_b=%s' if other is not None else '(v.team_a=%s OR v.team_b=%s)'
    args=tuple(sorted((key,other))) if other is not None else (key,key)
    return _query(session,f'''SELECT v.*,v.date::text AS date,s.record_class,s.playing_format,s.balls_per_group,
      s.competition_id,s.competition_label FROM {SCHEMA}.team_recent_matches cached
      JOIN {SCHEMA}.match_results v USING(match_key,scope_key,team_a,team_b)
      JOIN {PLAYER}.scopes s ON s.key=v.scope_key WHERE {pair} AND {where}
      ORDER BY v.date DESC NULLS LAST,v.match_id DESC''',(*args,*params))


def _summary_rows(session,key,other,filters,*,seasons=True):
    relation='team_vs_team_seasons' if seasons else 'team_vs_team'
    where,params=_where(filters,year='v.year' if seasons else 'NULL::integer')
    pair='v.team_a=%s AND v.team_b=%s' if other is not None else '(v.team_a=%s OR v.team_b=%s)'
    args=tuple(sorted((key,other))) if other is not None else (key,key)
    season='se.season,' if seasons else ''
    join=f'JOIN {SCHEMA}.seasons se ON se.key=v.season_key' if seasons else ''
    order='v.year DESC NULLS LAST,se.season NULLS LAST,' if seasons else ''
    return _query(session,f"""SELECT v.*,{season}s.record_class,s.playing_format,s.balls_per_group,s.competition_id,s.competition_label
      FROM {SCHEMA}.{relation} v {join} JOIN {PLAYER}.scopes s ON s.key=v.scope_key
      WHERE {pair} AND {where} ORDER BY {order}v.scope_key,v.team_a,v.team_b""",(*args,*params))


def _results(rows,key,h2h=False):
    r=dict.fromkeys(('matches','wins','losses','ties','draws','no_results','unclassified','decided_ties'),0)
    for row in rows:
        r['wins']+=row['a_wins'] if row['team_a']==key else row['b_wins']
        r['losses']+=row['b_wins'] if row['team_a']==key else row['a_wins']
        for col in ('matches','ties','draws','no_results','unclassified','decided_ties'):r[col]+=row[col]
    if h2h:r['first_wins']=r.pop('wins');r['second_wins']=r.pop('losses')
    return r


def _breakdowns(rows,key,h2h=False):
    output={}
    for name,columns in [('by_scope',('scope_key','record_class','playing_format','balls_per_group','competition_id','competition_label')),
                         ('by_year',('year',)),('seasons',('season',))]:
        groups=defaultdict(list)
        for row in rows:groups[tuple(row[col] for col in columns)].append(row)
        output[name]=[{**dict(zip(columns,k)),**_results(group,key,h2h)} for k,group in groups.items()]
    return output


def _innings(session,matches):
    return _query(session,f'SELECT * FROM {SCHEMA}.ordinary_innings WHERE match_key=ANY(%s) ORDER BY match_key,innings_number',
      ([v['match_key'] for v in matches],)) if matches else []


def _innings_stats(rows):
    first=[r['team_runs'] for r in rows if r['innings_number']==1];second=[r['team_runs'] for r in rows if r['innings_number']==2]
    low=[r['team_runs'] for r in rows if r['team_wickets']==10]
    return {'standard_innings':len(rows),'first_score_sum':sum(first),'first_score_count':len(first),
      'second_score_sum':sum(second),'second_score_count':len(second),
      'first_innings_mean':round(sum(first)/len(first),2) if first else None,
      'second_innings_mean':round(sum(second)/len(second),2) if second else None,
      'highest_team_total':max((r['team_runs'] for r in rows),default=None),'lowest_ten_wicket_total':min(low,default=None),
      'lowest_basis':'observed_standard_innings_with_ten_team_wickets'}


def _recent(matches,innings):
    groups=defaultdict(list)
    for row in innings:groups[row['match_key']].append(row)
    return [{'match_id':m['match_id'],'date':m['date'],'season':m['season'],'venue':m['venue'],'city':m['city'],
      'venue_country':m['venue_country'],'record_class':m['record_class'],'playing_format':m['playing_format'],
      'balls_per_group':m['balls_per_group'],'competition_id':m['competition_id'],'competition_label':m['competition_label'],
      'teams':[m['name_a'],m['name_b']],'result':RESULT_NAMES[m['result_code']],
      'decision_winner':m['name_a'] if m['result_code'] in(1,7) else m['name_b'] if m['result_code'] in(2,8) else None,
      'regulation_tie':m['result_code'] in(3,7,8),'outcome':m['outcome'],'match_stage':m['meta'].get('match_stage'),
      'innings':groups[m['match_key']]} for m in matches[:15]]


def _leaders(session,matches,team=None):
    if not matches:return {'batting':[],'bowling':[]}
    rows=_query(session,f'''SELECT a.*,ms.match_id,ms.date::text AS date,p.player_id,p.name,
      CASE WHEN a.role=1 THEN i.batting_team ELSE i.bowling_team END AS team
      FROM {BASE}.actor_innings a JOIN {BASE}.innings i USING(match_key,innings_number)
      JOIN {PLAYER}.match_scope ms USING(match_key) JOIN {BASE}.player_map mp ON mp.key=a.player_key
      JOIN {BASE}.players p USING(player_id) WHERE i.standard_stats AND a.match_key=ANY(%s)
      AND (%s IS NULL OR CASE WHEN a.role=1 THEN i.batting_team ELSE i.bowling_team END=%s)
      ORDER BY ms.date DESC NULLS LAST,ms.match_id,a.innings_number DESC''',([m['match_key'] for m in matches],team,team))
    groups=defaultdict(list)
    for row in rows:groups[(row['role'],row['player_id'])].append(row)
    output={'batting':[],'bowling':[]}
    for (role,pid),group in groups.items():
        output['batting' if role==1 else 'bowling'].append({'player_id':pid,'name':group[0]['name'],
          'observed_teams':sorted({r['team'] for r in group}),**actor_totals(group,role)})
    output['batting'].sort(key=lambda r:(-r['runs'],r['player_id']))
    output['bowling'].sort(key=lambda r:(-r['wickets'],r['runs_conceded'],r['player_id']))
    return {role:rows[:15] for role,rows in output.items()}


def read_team_h2h(session,first:str,second:str,filters:PlayerFilters):
    authority=assert_team_read(session);_validate_filters(session,filters);key=_team(session,first);other=_team(session,second)
    if key==other:raise InvalidPlayerFilters('Two different teams required')
    matches=_matches(session,key,other,filters);rows=_summary_rows(session,key,other,filters);innings=_innings(session,matches)
    return {'first_team':first,'second_team':second,'filters':asdict(filters),'source_run_id':authority['run_id'],
      'availability':'recorded_matches' if matches else 'no_recorded_matches','results':_results(_summary_rows(session,key,other,filters,seasons=False) if filters.year is None else rows,key,True),
      **_breakdowns(rows,key,True),'innings':_innings_stats(innings),'first_match':min((m['date'] for m in matches if m['date']),default=None),
      'last_match':max((m['date'] for m in matches if m['date']),default=None),'recent':_recent(matches,innings),
      'leaders':_leaders(session,matches),'coverage':_coverage(authority)}


def read_team_dashboard(session,team:str,filters:PlayerFilters):
    authority=assert_team_read(session);_validate_filters(session,filters);key=_team(session,team)
    matches=_matches(session,key,None,filters);rows=_summary_rows(session,key,None,filters);innings=_innings(session,matches)
    opposition=defaultdict(list)
    names={r['key']:r['name'] for r in _query(session,'SELECT key,name FROM cricket_opposition_read_v1.teams')}
    for row in rows:opposition[names[row['team_b'] if row['team_a']==key else row['team_a']]].append(row)
    venues=defaultdict(list)
    for m in matches:venues[(m['venue'],m['city'],m['venue_country'])].append(m)
    def match_counts(items):
        counters=[]
        for m in items:
            code=m['result_code'];counters.append({'team_a':m['team_a'],'matches':1,'a_wins':int(code in(1,7)),
              'b_wins':int(code in(2,8)),'ties':int(code==3),'draws':int(code==4),'no_results':int(code==5),
              'unclassified':int(code==6),'decided_ties':int(code in(7,8))})
        return _results(counters,key)
    form=[]
    for m in matches[:15]:
        code=m['result_code'];won=(m['team_a']==key and code in(1,7)) or(m['team_b']==key and code in(2,8))
        form.append('win' if won else 'loss' if code in(1,2,7,8) else RESULT_NAMES[code])
    streak=0
    if form and form[0] in('win','loss'):
        for value in form:
            if value!=form[0]:break
            streak+=1
    return {'team':team,'filters':asdict(filters),'source_run_id':authority['run_id'],'availability':'recorded_matches' if matches else 'no_recorded_matches',
      'results':_results(_summary_rows(session,key,None,filters,seasons=False) if filters.year is None else rows,key),**_breakdowns(rows,key),
      'by_opposition':[{'opposition':name,**_results(group,key)} for name,group in sorted(opposition.items())],
      'venues':[{'venue':k[0],'city':k[1],'country':k[2],**match_counts(v)} for k,v in venues.items()],
      'innings':_innings_stats([i for i in innings if i['batting_team']==team]),'recent':_recent(matches,innings),
      'form':form,'streak':{'kind':form[0] if streak else None,'count':streak,'basis':'at_most_fifteen_latest_recorded_matches'},
      'leaders':_leaders(session,matches,team),'coverage':_coverage(authority),
      'location_classification':{'status':'unavailable_without_team_home_evidence'},
      'rankings':{'status':'unavailable_without_verified_ranking_source'},
      'achievements':{'status':'official_completeness_not_proved'},'net_run_rate':{'value':None,'status':'competition_rules_not_proved'}}


def _venue_totals(rows):
    cols=('matches','decisive_matches','ties','draws','no_results','unclassified','standard_innings',
      'first_score_sum','first_score_count','second_score_sum','second_score_count','chase_decided_matches','chase_wins')
    totals={col:sum(r[col] for r in rows) for col in cols}
    for n in ('first','second'):
        totals[n+'_innings_mean']=round(totals[n+'_score_sum']/totals[n+'_score_count'],2) if totals[n+'_score_count'] else None
    totals['highest_team_total']=max((r['highest_team_total'] for r in rows if r['highest_team_total'] is not None),default=None)
    totals['lowest_ten_wicket_total']=min((r['lowest_ten_wicket_total'] for r in rows if r['lowest_ten_wicket_total'] is not None),default=None)
    totals['chasing_win_pct']=round(totals['chase_wins']*100/totals['chase_decided_matches'],2) if totals['chase_decided_matches'] else None
    return totals


def read_venue(session,venue_key:int,filters:PlayerFilters):
    authority=assert_team_read(session);_validate_filters(session,filters)
    if type(venue_key) is not int or not 1<=venue_key<=32767:raise InvalidPlayerFilters('Positive raw venue key required')
    venues=_query(session,f'SELECT * FROM {SCHEMA}.venues WHERE key=%s',(venue_key,))
    if not venues:raise LookupError('Unknown raw venue')
    where,params=_where(filters)
    rows=_query(session,f'''SELECT v.*,s.record_class,s.playing_format,s.balls_per_group,s.competition_id,s.competition_label
      FROM {SCHEMA}.venue_stats v JOIN {PLAYER}.scopes s ON s.key=v.scope_key
      WHERE v.venue_key=%s AND {where} ORDER BY v.year DESC NULLS LAST,v.scope_key''',(venue_key,*params))
    return {**venues[0],'filters':asdict(filters),'source_run_id':authority['run_id'],
      'availability':'recorded_matches' if rows else 'no_recorded_matches','totals':_venue_totals(rows),
      'rows':[{**row,**_venue_totals([row])} for row in rows],'coverage':{**_coverage(authority),
      'chase_basis':CHASE_BASIS,'lowest_basis':'observed_standard_innings_with_ten_team_wickets','complete_all_out_eligibility':'not_proved'}}


def read_team_metadata(session):
    a=assert_team_read(session)
    return {'source_run_id':a['run_id'],'coverage':_coverage(a),
      'teams':_query(session,'SELECT key,name FROM cricket_opposition_read_v1.teams ORDER BY name'),
      'venues':_query(session,f'SELECT * FROM {SCHEMA}.venues ORDER BY venue NULLS LAST,city NULLS LAST,country NULLS LAST'),
      'seasons':_query(session,f'SELECT key,season FROM {SCHEMA}.seasons ORDER BY season NULLS LAST')}


def _card_payloads(session,filters):
    if filters==PlayerFilters():return _query(session,f'SELECT stat_id,payload FROM {SCHEMA}.stat_cards ORDER BY stat_id')
    where,params=_where(filters)
    selected=_query(session,f'SELECT 1 FROM {SCHEMA}.match_results v JOIN {PLAYER}.scopes s ON s.key=v.scope_key WHERE {where} LIMIT 1',params)
    if not selected:return []
    specs=[('most_t20_sixes','six_candidates',f'''SELECT jsonb_build_object('player_key',player_key,'value',SUM(value)) payload FROM filtered GROUP BY player_key HAVING SUM(value)>0 ORDER BY SUM(value) DESC,player_key LIMIT 1'''),
      ('highest_total','total_candidates',"SELECT jsonb_build_object('team_key',team_key,'value',team_runs,'match_key',match_key,'innings_number',innings_number,'scope_key',scope_key) payload FROM filtered ORDER BY team_runs DESC,match_key,innings_number LIMIT 1"),
      ('best_figures','figure_candidates',"SELECT jsonb_build_object('player_key',player_key,'team_key',team_key,'wickets',dismissals,'runs_conceded',runs,'match_key',match_key,'innings_number',innings_number,'scope_key',scope_key) payload FROM filtered ORDER BY dismissals DESC,runs,date DESC NULLS LAST,match_id,innings_number,player_key LIMIT 1"),
      ('most_matches','activity_candidates',"SELECT jsonb_build_object('player_key',player_key,'value',COUNT(DISTINCT match_key)) payload FROM filtered GROUP BY player_key ORDER BY COUNT(DISTINCT match_key) DESC,player_key LIMIT 1")]
    result=[]
    for stat_id,view,query in specs:
        rows=_query(session,f'WITH filtered AS(SELECT v.* FROM {SCHEMA}.{view} v JOIN {PLAYER}.scopes s ON s.key=v.scope_key WHERE {where}) '+query,params)
        result.append({'stat_id':stat_id,'payload':rows[0]['payload'] if rows else None})
    return result


def read_stat_cards(session,filters:PlayerFilters):
    authority=assert_team_read(session);_validate_filters(session,filters);cards=[]
    players={r['key']:r for r in _query(session,f'SELECT pm.key,p.player_id,p.name FROM {BASE}.player_map pm JOIN {BASE}.players p USING(player_id)')}
    teams={r['key']:r['name'] for r in _query(session,'SELECT key,name FROM cricket_opposition_read_v1.teams')}
    matches={r['key']:r['match_id'] for r in _query(session,f'SELECT key,match_id FROM {BASE}.match_map')}
    scopes={r['key']:r for r in _query(session,f'SELECT * FROM {PLAYER}.scopes')}
    labels={'most_t20_sixes':'Most six-run deliveries in recorded T20 matches','highest_total':'Highest recorded standard team total',
      'best_figures':'Best recorded standard innings bowling figures','most_matches':'Most matches with observed batting or bowling'}
    for item in _card_payloads(session,filters):
        payload=item['payload'];card={'stat_id':item['stat_id'],'label':labels[item['stat_id']],
          'availability':'observed_qualifying_activity' if payload else 'no_observed_qualifying_activity','value':None}
        if payload:
            card.update(payload)
            if 'player_key' in payload:card.update({k:v for k,v in players[payload['player_key']].items() if k!='key'})
            if 'team_key' in payload:card['team']=teams[payload['team_key']]
            if 'match_key' in payload:card['match_id']=matches[payload['match_key']]
            if 'scope_key' in payload:card['scope']={k:v for k,v in scopes[payload['scope_key']].items() if k!='key'}
        if item['stat_id']=='most_matches':card['appearance_basis']='observed_standard_batting_or_bowling'
        cards.append(card)
    return {'filters':asdict(filters),'source_run_id':authority['run_id'],'cards':cards,'coverage':_coverage(authority)}
