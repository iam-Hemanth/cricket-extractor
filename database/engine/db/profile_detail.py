"""Detailed private profile reads with evidence-labelled coverage and exact counts."""
from db.player_read import PlayerFilters,_query,_where,_resolve,_validate_filters
from db.player_summary import SCHEMA,BASE,assert_player_read


def _rates(row,role):
    from decimal import Decimal,ROUND_HALF_UP
    def ratio(n,d,m=1):return None if not d else float((Decimal(n)*m/Decimal(d)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    if role==1:
        row.update(average=ratio(row['runs'],row['dismissals']),strike_rate=ratio(row['runs'],row['balls_faced'],100))
    else:
        row.update(economy=ratio(row['runs_conceded'],row['legal_balls'],6),bowling_average=ratio(row['runs_conceded'],row['wickets']),bowling_strike_rate=ratio(row['legal_balls'],row['wickets']))
    return row


def _phases(session,key,role,filters):
    where,params=_where(filters)
    common=f'''WITH targets AS(SELECT v.match_key,v.innings_number FROM {SCHEMA}.actor_rows v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key
       WHERE v.player_key=%s AND v.role=%s AND v.standard_stats AND {where}),
       fact AS(SELECT b.match_key,b.innings_number,d.* FROM targets t JOIN {BASE}.blocks b USING(match_key,innings_number)
       CROSS JOIN LATERAL {BASE}.decode_records(b.payload,b.records::integer,b.start_ordinal::integer) d),
       events AS MATERIALIZED(SELECT e.*,mp.key AS match_key FROM {BASE}.dismissal_events e JOIN {BASE}.match_map mp USING(match_id) JOIN targets t ON t.match_key=mp.key AND t.innings_number=e.innings_number)'''
    if role==1:
        statement=common+f''', activity AS(SELECT phase,SUM(scored_batter)::bigint AS runs,
           COUNT(*) FILTER(WHERE observed_wides=0) AS balls_faced,
           COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=4) AS four_run_deliveries,
           COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=6) AS six_run_deliveries
           FROM fact WHERE batter=%s GROUP BY phase),outs AS(
           SELECT f.phase,SUM(e.batter_dismissals)::bigint AS dismissals FROM events e JOIN fact f USING(match_key,innings_number)
           WHERE e.delivery_ordinal=f.ordinal AND e.player_out_id=(SELECT player_id FROM {BASE}.player_map WHERE key=%s) GROUP BY f.phase)
           SELECT COALESCE(a.phase,o.phase) AS phase_key,COALESCE(a.runs,0)::bigint AS runs,COALESCE(a.balls_faced,0)::bigint AS balls_faced,
           COALESCE(o.dismissals,0)::bigint AS dismissals,COALESCE(a.four_run_deliveries,0)::bigint AS four_run_deliveries,COALESCE(a.six_run_deliveries,0)::bigint AS six_run_deliveries
           FROM activity a FULL JOIN outs o USING(phase) ORDER BY phase_key'''
    else:
        statement=common+f''', activity AS(SELECT phase,SUM(scored_batter+scored_wides+scored_noballs)::bigint AS runs_conceded,
           COUNT(*) FILTER(WHERE observed_wides=0 AND observed_noballs=0) AS legal_balls,
           COUNT(*) FILTER(WHERE scored_batter=4) AS four_run_deliveries,COUNT(*) FILTER(WHERE scored_batter=6) AS six_run_deliveries
           FROM fact WHERE bowler=%s GROUP BY phase),outs AS(
           SELECT f.phase,SUM(e.bowler_wickets)::bigint AS wickets FROM events e JOIN fact f USING(match_key,innings_number)
           WHERE e.delivery_ordinal=f.ordinal AND f.bowler=%s GROUP BY f.phase)
           SELECT a.phase AS phase_key,a.runs_conceded,a.legal_balls,COALESCE(o.wickets,0)::bigint AS wickets,
           a.four_run_deliveries,a.six_run_deliveries FROM activity a LEFT JOIN outs o USING(phase) ORDER BY phase_key'''
    rows=_query(session,statement,(key,role,*params,key,key))
    labels={r['id']:r['value'] for r in _query(session,f"SELECT id,value FROM {BASE}.dictionary WHERE name='phase'")}
    for row in rows:
        row['phase']=labels.get(row.pop('phase_key'),'unknown');_rates(row,role)
    return rows


def _participation(session,pid,filters):
    where,params=_where(filters,year='ms.year')
    rows=_query(session,f'''WITH listed AS(SELECT p.match_id,ms.match_key FROM {BASE}.participant_records p JOIN {SCHEMA}.match_scope ms USING(match_id)
      JOIN {SCHEMA}.scopes s ON s.key=ms.scope_key WHERE p.player_id=%s AND {where}),
      evidence AS(SELECT l.*,
       EXISTS(SELECT 1 FROM {BASE}.lineup_members lm JOIN {BASE}.lineup_assertions la USING(match_id,team_name) WHERE lm.match_id=l.match_id AND lm.player_id=%s AND la.status='confirmed') AS confirmed_selection,
       EXISTS(SELECT 1 FROM {BASE}.actor_innings a JOIN {BASE}.player_map p ON p.key=a.player_key WHERE a.match_key=l.match_key AND p.player_id=%s) AS observed_activity,
       EXISTS(SELECT 1 FROM {BASE}.actor_innings a WHERE a.match_key=l.match_key) AS match_activity,
       EXISTS(SELECT 1 FROM {BASE}.participation_events e WHERE e.match_id=l.match_id AND e.incoming_player_id=%s
         AND e.event_type IN('impact_player','concussion_substitute') AND
         (e.source_facts->'confirmed'='true'::jsonb OR (jsonb_typeof(e.source_facts->'source_ball_id')='number' AND e.source_facts->>'source_ball_id'~'^[1-9][0-9]*$' AND jsonb_typeof(e.source_facts->'source_refs')='array' AND e.source_facts->'source_refs'<>'[]'::jsonb))) AS confirmed_incoming,
       EXISTS(SELECT 1 FROM {BASE}.participation_events e WHERE e.match_id=l.match_id AND e.event_type='unknown' AND (e.incoming_player_id=%s OR e.outgoing_player_id=%s)) AS unknown_event
       FROM listed l)
      SELECT COUNT(*) AS listed_matches,COUNT(*) FILTER(WHERE confirmed_selection) AS confirmed_xi_selection_matches,
       COUNT(*) FILTER(WHERE observed_activity) AS observed_activity_matches,COUNT(*) FILTER(WHERE confirmed_incoming) AS confirmed_incoming_matches,
       COUNT(*) FILTER(WHERE unknown_event) AS unknown_event_matches,
       COUNT(*) FILTER(WHERE observed_activity OR confirmed_incoming OR (confirmed_selection AND match_activity)) AS recorded_appearance_lower_bound,
       COUNT(*) FILTER(WHERE NOT(observed_activity OR confirmed_incoming OR (confirmed_selection AND match_activity))) AS unresolved_listed_matches FROM evidence''',
        (pid,*params,pid,pid,pid,pid,pid))[0]
    return {**rows,'basis':'confirmed_and_observed_recorded_subset_lower_bound','official_career_complete':False}


def read_profile_detail(session,player_id:str,filters:PlayerFilters):
    authority=assert_player_read(session);_validate_filters(session,filters);player=_resolve(session,player_id);pid=player['player_id']
    key=_query(session,f'SELECT key FROM {BASE}.player_map WHERE player_id=%s',(pid,))[0]['key']
    where,params=_where(filters)
    result={**player,'filters':{'record_class':filters.record_class,'playing_format':filters.playing_format,'competition_id':filters.competition_id,'year':filters.year},
        'source_run_id':authority['run_id'],'coverage':{'dataset_matches':authority['matches'],'held_matches':authority['held_count'],'full_admitted_snapshot_ready':authority['ready'],'economy_basis_balls':6},
        'awards':{'count':None,'status':'unavailable_in_verified_source'},
        'location_classification':{'status':'unclassified_without_team_home_evidence'},
        'participation':_participation(session,pid,filters),'recent':{},'venues':{},'test_splits':{},'phases':{}}
    for role,kind in ((1,'batting'),(2,'bowling')):
        fields=('v.runs AS runs,v.balls AS balls_faced,v.dismissals AS dismissals' if role==1 else 'v.runs AS runs_conceded,v.balls AS legal_balls,v.dismissals AS wickets')
        base=f'''FROM {SCHEMA}.actor_rows v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key
            JOIN {BASE}.matches m ON m.match_id=v.match_id JOIN {BASE}.innings i ON i.match_id=v.match_id AND i.innings_number=v.innings_number
            WHERE v.player_key=%s AND v.role=%s AND v.standard_stats AND {where}'''
        args=(key,role,*params)
        result['recent'][kind]=_query(session,f'''SELECT v.match_id,v.innings_number,v.date::text,{fields},s.playing_format,s.balls_per_group,
            m.venue,CASE WHEN v.role=1 THEN i.bowling_team ELSE i.batting_team END AS opposition {base}
            ORDER BY v.date DESC NULLS LAST,v.match_id,v.innings_number DESC LIMIT 10''',args)
        for row in result['recent'][kind]:_rates(row,role)
        sums=('SUM(v.runs)::bigint AS runs,SUM(v.balls)::bigint AS balls_faced,SUM(v.dismissals)::bigint AS dismissals' if role==1 else 'SUM(v.runs)::bigint AS runs_conceded,SUM(v.balls)::bigint AS legal_balls,SUM(v.dismissals)::bigint AS wickets')
        result['venues'][kind]=_query(session,f'''SELECT m.venue,m.city,m.venue_country,{sums},COUNT(*) AS observed_innings {base}
            GROUP BY m.venue,m.city,m.venue_country ORDER BY m.venue NULLS LAST,m.city NULLS LAST,m.venue_country NULLS LAST''',args)
        result['test_splits'][kind]=_query(session,f'''SELECT v.innings_number,{sums},COUNT(*) AS observed_innings {base}
            AND s.record_class='test' GROUP BY v.innings_number ORDER BY v.innings_number''',args)
        for row in result['venues'][kind]+result['test_splits'][kind]:_rates(row,role)
        result['phases'][kind]=_phases(session,key,role,filters)
    teams=_query(session,f'''SELECT team,COUNT(DISTINCT match_id) AS observed_matches FROM(
        SELECT v.match_id,CASE WHEN v.role=1 THEN i.batting_team ELSE i.bowling_team END AS team
        FROM {SCHEMA}.actor_rows v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key
        JOIN {BASE}.innings i ON i.match_id=v.match_id AND i.innings_number=v.innings_number
        WHERE v.player_key=%s AND v.standard_stats AND {where}) x GROUP BY team ORDER BY observed_matches DESC,team''',(key,*params))
    result['team_history']={'basis':'observed_batting_bowling_activity','rows':teams,'primary_observed_team':teams[0]['team'] if teams else None}
    return result
