"""Exact raw-team opposition reads; filtered details never use coarse totals."""
from dataclasses import asdict
import hashlib
from pathlib import Path
from db.player_summary import BASE,SCHEMA as PLAYER,assert_player_read
from db.player_read import PlayerFilters,InvalidPlayerFilters,_where,_query,_resolve,_validate_filters
from db.profile_detail import _rates
from ingestion import source_store as source

SCHEMA='cricket_opposition_read_v1'
SCHEMA_PATH=Path(__file__).with_name('opposition_summary.sql')


def assert_opposition_read(session):
    a=assert_player_read(session)
    with session.connection.cursor() as c:
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is None:raise ValueError('Opposition summaries are not installed')
        c.execute(f'SELECT ddl_sha256,source_run_id,manifest_sha256,codebook_sha256,cluster_run_id FROM {SCHEMA}.read_environment')
        if c.fetchall()!=[(source._sha(SCHEMA_PATH),a['run_id'],a['manifest_sha256'],a['codebook_sha256'],session.descriptor['run_id'])]:raise ValueError('Opposition read authority differs')
        c.execute("SELECT ispopulated FROM pg_matviews WHERE schemaname=%s AND matviewname='player_vs_team'",(SCHEMA,))
        if c.fetchall()!=[(True,)]:raise ValueError('Opposition summary unavailable')
        c.execute('''SELECT i.indisunique,i.indisvalid,i.indisready,i.indpred IS NULL,i.indexprs IS NULL,
          ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,pos) JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum ORDER BY k.pos)
          FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_class idx ON idx.oid=i.indexrelid
          JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname='player_vs_team' AND idx.relname='player_vs_team_key' ''',(SCHEMA,))
        if c.fetchall()!=[(True,True,True,True,True,['player_key','role','opposition_key'])]:raise ValueError('Opposition index authority differs')
    return a


def install_opposition_summaries(session):
    a=assert_player_read(session)
    with session.connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_opposition_install'))")
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is not None:raise ValueError('Opposition schema nonempty; refuse overwrite')
        c.execute("SET LOCAL statement_timeout='60s';SET LOCAL jit=off")
        raw=SCHEMA_PATH.read_bytes();c.execute(raw.decode())
        c.execute(f'INSERT INTO {SCHEMA}.read_environment VALUES(true,%s,%s,%s,%s,%s)',(hashlib.sha256(raw).hexdigest(),a['run_id'],a['manifest_sha256'],a['codebook_sha256'],session.descriptor['run_id']))
    assert_opposition_read(session);return {'materialized_views':1,'source_run_id':a['run_id']}


def _totals(rows,role):
    result={'observed_matches':len({r['match_id'] for r in rows}),'observed_innings':len(rows)}
    if role==1:
        result.update(runs=sum(r['runs'] for r in rows),balls_faced=sum(r['balls'] for r in rows),dismissals=sum(r['dismissals'] for r in rows),
            fifties=sum(50<=r['runs']<100 for r in rows),hundreds=sum(r['runs']>=100 for r in rows),ducks=sum(r['runs']==0 and r['dismissals']>0 for r in rows),
            highest_score=max((r['runs'] for r in rows),default=None))
        result['highest_score_not_out']=None if not rows else any(r['runs']==result['highest_score'] and not r['dismissals'] for r in rows)
    else:
        matchwickets={}
        for r in rows:matchwickets[r['match_id']]=matchwickets.get(r['match_id'],0)+r['dismissals']
        best=min(rows,key=lambda r:(-r['dismissals'],r['runs'],r['date'] is None,-int(r['date'].replace('-','')) if r['date'] else 0,r['match_id'],r['innings_number'])) if rows else None
        result.update(runs_conceded=sum(r['runs'] for r in rows),legal_balls=sum(r['balls'] for r in rows),wickets=sum(r['dismissals'] for r in rows),
            five_wicket_innings=sum(r['dismissals']>=5 for r in rows),ten_wicket_matches=sum(w>=10 for w in matchwickets.values()),
            best_figures={'wickets':best['dismissals'],'runs_conceded':best['runs'],'match_id':best['match_id'],'innings_number':best['innings_number']} if best else None)
    return _rates(result,role)


def _slices(session,key,role,team_key,filters):
    where,params=_where(filters)
    common=f'''WITH targets AS(SELECT v.match_key,v.innings_number FROM {SCHEMA}.actor_opposition v JOIN {PLAYER}.scopes s ON s.key=v.scope_key
      WHERE v.player_key=%s AND v.role=%s AND v.opposition_key=%s AND v.standard_stats AND {where}),
      fact AS MATERIALIZED(SELECT b.match_key,b.innings_number,f.* FROM targets t JOIN {BASE}.blocks b USING(match_key,innings_number) CROSS JOIN LATERAL {BASE}.decode_records(b.payload,b.records::integer,b.start_ordinal::integer) f),
      events AS MATERIALIZED(SELECT e.*,m.key AS match_key FROM {BASE}.dismissal_events e JOIN {BASE}.match_map m USING(match_id) JOIN targets t ON t.match_key=m.key AND t.innings_number=e.innings_number)'''
    if role==1:
        query=common+f''', activity AS(SELECT phase,SUM(scored_batter)::bigint AS runs,COUNT(*) FILTER(WHERE observed_wides=0) AS balls_faced,
          COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=0) AS zero_batter_run_balls,COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=4) AS four_run_deliveries,COUNT(*) FILTER(WHERE observed_wides=0 AND scored_batter=6) AS six_run_deliveries
          FROM fact WHERE batter=%s GROUP BY phase),outs AS(SELECT f.phase,SUM(e.batter_dismissals)::bigint AS dismissals FROM events e JOIN fact f USING(match_key,innings_number) WHERE f.ordinal=e.delivery_ordinal AND e.player_out_id=(SELECT player_id FROM {BASE}.player_map WHERE key=%s) GROUP BY f.phase)
          SELECT COALESCE(a.phase,o.phase) AS phase_key,COALESCE(runs,0)::bigint AS runs,COALESCE(balls_faced,0)::bigint AS balls_faced,COALESCE(dismissals,0)::bigint AS dismissals,COALESCE(zero_batter_run_balls,0)::bigint AS zero_batter_run_balls,COALESCE(four_run_deliveries,0)::bigint AS four_run_deliveries,COALESCE(six_run_deliveries,0)::bigint AS six_run_deliveries FROM activity a FULL JOIN outs o USING(phase)'''
        out_where='e.player_out_id=(SELECT player_id FROM '+BASE+'.player_map WHERE key=%s)'
        credit='batter_dismissals'
    else:
        query=common+f''', activity AS(SELECT phase,SUM(scored_batter+scored_wides+scored_noballs)::bigint AS runs_conceded,COUNT(*) FILTER(WHERE observed_wides=0 AND observed_noballs=0) AS legal_balls,
          COUNT(*) FILTER(WHERE observed_wides=0 AND observed_noballs=0 AND scored_batter+scored_wides+scored_noballs=0) AS zero_charge_legal_balls,COUNT(*) FILTER(WHERE scored_batter=4) AS four_run_deliveries,COUNT(*) FILTER(WHERE scored_batter=6) AS six_run_deliveries FROM fact WHERE bowler=%s GROUP BY phase),
          outs AS(SELECT f.phase,SUM(e.bowler_wickets)::bigint AS wickets FROM events e JOIN fact f USING(match_key,innings_number) WHERE f.ordinal=e.delivery_ordinal AND f.bowler=%s GROUP BY f.phase)
          SELECT a.phase AS phase_key,a.runs_conceded,a.legal_balls,COALESCE(o.wickets,0)::bigint AS wickets,a.zero_charge_legal_balls,a.four_run_deliveries,a.six_run_deliveries FROM activity a LEFT JOIN outs o USING(phase)'''
        out_where='f.bowler=%s';credit='bowler_wickets'
    args=(key,role,team_key,*params)
    rows=_query(session,query,(*args,key,key));labels={r['id']:r['value'] for r in _query(session,f"SELECT id,value FROM {BASE}.dictionary WHERE name='phase'")}
    for r in rows:r['phase']=labels.get(r.pop('phase_key'),'unknown');_rates(r,role)
    dismissals=_query(session,common+f''' SELECT e.kind,SUM(e.{credit})::bigint AS {'dismissals' if role==1 else 'wickets'} FROM events e JOIN fact f USING(match_key,innings_number)
        WHERE f.ordinal=e.delivery_ordinal AND {out_where} GROUP BY e.kind ORDER BY e.kind''',(*args,key))
    return sorted(rows,key=lambda r:r['phase']),dismissals


def read_player_vs_team(session,player_id:str,team:str,mode:str,filters:PlayerFilters):
    authority=assert_opposition_read(session);_validate_filters(session,filters);p=_resolve(session,player_id)
    if mode not in ('auto','batting','bowling'):raise InvalidPlayerFilters('Unknown role mode')
    if type(team) is not str or not team:raise InvalidPlayerFilters('Exact raw team name required')
    t=_query(session,f'SELECT key,name FROM {SCHEMA}.teams WHERE name=%s',(team,))
    if not t:raise InvalidPlayerFilters('Unknown exact raw team')
    t=t[0];key=_query(session,f'SELECT key FROM {BASE}.player_map WHERE player_id=%s',(p['player_id'],))[0]['key']
    where,params=_where(filters)
    allrows=_query(session,f'''SELECT v.*,v.date::text AS date,m.venue,m.city,m.venue_country,s.record_class,s.playing_format,s.balls_per_group,s.competition_id,s.competition_label FROM {SCHEMA}.actor_opposition v
        JOIN {PLAYER}.scopes s ON s.key=v.scope_key JOIN {BASE}.matches m ON m.match_id=v.match_id
        WHERE v.player_key=%s AND v.opposition_key=%s AND v.standard_stats AND {where}
        ORDER BY v.date DESC NULLS LAST,v.match_id,v.innings_number DESC''',(key,t['key'],*params))
    batting=[r for r in allrows if r['role']==1];bowling=[r for r in allrows if r['role']==2]
    primary='bowling' if sum(r['balls'] for r in bowling)>sum(r['balls'] for r in batting) else 'batting'
    active=primary if mode=='auto' else mode;role=1 if active=='batting' else 2;selected=batting if role==1 else bowling
    def grouped(columns):
        groups={}
        for row in selected:groups.setdefault(tuple(row[c] for c in columns),[]).append(row)
        return [{**dict(zip(columns,k)),**_totals(v,role)} for k,v in groups.items()]
    phases,dismissals=_slices(session,key,role,t['key'],filters)
    recent=[]
    for r in selected[:10]:recent.append({**r,**_totals([r],role)})
    unknown_where,unknown_params=_where(filters,year='ms.year')
    unknown=_query(session,f'''SELECT COALESCE(SUM(i.source_only_timed_out),0)::bigint AS quantity
      FROM {BASE}.participant_records pr JOIN {PLAYER}.match_scope ms USING(match_id)
      JOIN {PLAYER}.scopes s ON s.key=ms.scope_key JOIN {BASE}.innings i USING(match_id)
      WHERE pr.player_id=%s AND i.standard_stats AND
      CASE WHEN %s=1 THEN i.bowling_team ELSE i.batting_team END=%s AND {unknown_where}''',
      (p['player_id'],role,t['name'],*unknown_params))[0]['quantity']
    return {**p,'team':t['name'],'mode':mode,'active_mode':active,'primary_observed_role':primary,'filters':asdict(filters),'source_run_id':authority['run_id'],
      'availability':'observed_standard_innings' if selected else 'no_observed_standard_innings','totals':_totals(selected,role),
      'by_scope':grouped(('scope_key','record_class','playing_format','balls_per_group','competition_id','competition_label')),
      'by_year':grouped(('year',)),'venues':grouped(('venue','city','venue_country')),'phases':phases,'dismissals':dismissals,'recent':recent,
      'maidens':{'value':None,'status':'complete_group_source_evidence_not_proved'},
      'awards':{'count':None,'status':'unavailable_in_verified_source'},
      'location_classification':{'status':'unclassified_without_team_home_evidence'},
      'coverage':{'dataset_matches':authority['matches'],'held_matches':authority['held_count'],'full_admitted_snapshot_ready':authority['ready'],
       'appearance_basis':'observed_role_activity','economy_basis_balls':6,'team_identity':'exact_raw_source_name','eligibility':'source_standard_stats',
       'unattributed_team_wickets_in_listed_opposition_innings':unknown,
       'unattributed_wicket_basis':'source_only_team_quantities_in_listed_candidate_innings_not_assigned_to_player'}}
