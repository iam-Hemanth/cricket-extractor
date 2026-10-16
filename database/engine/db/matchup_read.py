"""Guarded directional batter/bowler summaries and bounded pair detail reads."""
from dataclasses import asdict
from decimal import Decimal,ROUND_HALF_UP
import hashlib
from pathlib import Path

from db.source_connection import ProofSession
from db.player_summary import BASE,SCHEMA as PLAYER_SCHEMA,assert_player_read
from db.player_read import PlayerFilters,InvalidPlayerFilters,_query,_resolve,_validate_filters,_where
from ingestion import source_store as source

SCHEMA='cricket_matchup_read_v1'
SCHEMA_PATH=Path(__file__).with_name('matchup_summary.sql')
COUNTERS=('runs','balls_faced','legal_balls','zero_run_balls','four_run_balls','six_run_balls','dismissals')


def assert_matchup_read(session):
    authority=assert_player_read(session)
    with session.connection.cursor() as c:
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is None:raise ValueError('Matchup summaries are not installed')
        c.execute(f'SELECT ddl_sha256,source_run_id,manifest_sha256,codebook_sha256,cluster_run_id FROM {SCHEMA}.read_environment')
        expected=(source._sha(SCHEMA_PATH),authority['run_id'],authority['manifest_sha256'],authority['codebook_sha256'],session.descriptor['run_id'])
        if c.fetchall()!=[expected]:raise ValueError('Matchup read authority differs')
        c.execute('SELECT matviewname,ispopulated FROM pg_matviews WHERE schemaname=%s ORDER BY matviewname',(SCHEMA,))
        if c.fetchall()!=[('batter_vs_bowler',True)]:raise ValueError('Matchup summary is not populated')
        c.execute('''SELECT idx.relname,i.indisunique,i.indisvalid,i.indisready,i.indnullsnotdistinct,
            ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,pos)
            JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum ORDER BY k.pos),
            i.indpred IS NULL,i.indexprs IS NULL,am.amname,i.indnkeyatts,i.indnatts
            FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_am am ON am.oid=idx.relam
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=%s AND t.relkind='m' ORDER BY idx.relname''',(SCHEMA,))
        expected=[('batter_vs_bowler_key',True,True,True,True,
            ['batter_key','bowler_key','scope_key','year','phase_code'],True,True,'btree',5,5)]
        if c.fetchall()!=expected:raise ValueError('Matchup index authority differs')
    return authority


def install_matchup_summaries(session:ProofSession)->dict:
    authority=assert_player_read(session)
    from db.compact_verify import _stored_codebook
    _,_,digest=_stored_codebook(session.connection)
    if digest!=authority['codebook_sha256']:raise ValueError('Base codebook differs')
    with session.connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('cricstats_matchup_read_install'))")
        c.execute('SELECT to_regnamespace(%s)',(SCHEMA,))
        if c.fetchone()[0] is not None:raise ValueError('Matchup schema is nonempty; refuse overwrite')
        c.execute("SET LOCAL statement_timeout='120s';SET LOCAL jit=off")
        raw=SCHEMA_PATH.read_bytes();c.execute(raw.decode())
        c.execute(f'INSERT INTO {SCHEMA}.read_environment VALUES(true,%s,%s,%s,%s,%s)',
            (hashlib.sha256(raw).hexdigest(),authority['run_id'],authority['manifest_sha256'],authority['codebook_sha256'],session.descriptor['run_id']))
    assert_matchup_read(session)
    return {'materialized_views':1,'source_run_id':authority['run_id'],'scope':'all observed standard directional pair buckets'}


def _rates(counts):
    def ratio(numerator,denominator,multiplier=1):
        if not denominator:return None
        return float((Decimal(numerator)*multiplier/Decimal(denominator)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    return {**counts,'average':ratio(counts['runs'],counts['dismissals']),
        'strike_rate':ratio(counts['runs'],counts['balls_faced'],100),
        'zero_run_percentage':ratio(counts['zero_run_balls'],counts['balls_faced'],100),
        'four_run_percentage':ratio(counts['four_run_balls'],counts['balls_faced'],100),
        'six_run_percentage':ratio(counts['six_run_balls'],counts['balls_faced'],100)}


def _details(session,batter_key,bowler_key,batter_id,filters,phase_code):
    where,params=_where(filters,year='ms.year')
    # Actor cache has role/player as its leading primary key; event candidates
    # include an actual out actor who never faced this bowler as striker.
    return _query(session,f'''WITH observed AS (
        SELECT a.match_key,a.innings_number FROM {BASE}.actor_innings a
        JOIN {BASE}.actor_innings b USING(match_key,innings_number)
        WHERE a.role=1 AND a.player_key=%s AND b.role=2 AND b.player_key=%s
        UNION
        SELECT ms.match_key,e.innings_number FROM {BASE}.dismissal_events e
        JOIN {PLAYER_SCHEMA}.match_scope ms USING(match_id)
        JOIN {BASE}.blocks b ON b.match_key=ms.match_key AND b.innings_number=e.innings_number
        AND e.delivery_ordinal>=b.start_ordinal AND e.delivery_ordinal<b.start_ordinal::integer+b.records
        WHERE NOT e.source_only AND e.player_out_id=%s
        AND {BASE}.u16(b.payload,(e.delivery_ordinal-b.start_ordinal)*55+6)=%s
    ), candidates AS MATERIALIZED (
        SELECT ms.match_id,c.match_key,c.innings_number,ms.scope_key,ms.year,ms.date::text AS date,
            m.venue,s.record_class,s.playing_format,s.balls_per_group,s.competition_id,s.competition_label
        FROM observed c JOIN {BASE}.innings i USING(match_key,innings_number)
        JOIN {PLAYER_SCHEMA}.match_scope ms USING(match_key)
        JOIN {PLAYER_SCHEMA}.scopes s ON s.key=ms.scope_key JOIN {BASE}.matches m ON m.match_id=ms.match_id
        WHERE i.standard_stats AND {where}
    ), pair_blocks AS MATERIALIZED (
        SELECT c.*,b.payload,b.records,b.start_ordinal FROM candidates c
        JOIN {BASE}.blocks b USING(match_key,innings_number)
    ) SELECT c.*,f.*,dp.value AS phase,dn.value AS nominal_phase,
        dm.value AS source_marker,du.value AS source_unique_marker,
        NULLIF(f.source_group,65535) AS source_group_number,bat.player_id AS delivery_batter_id,
        partner.player_id AS non_striker_id,COALESCE(events.items,'[]'::jsonb) AS dismissal_events
        FROM pair_blocks c
        CROSS JOIN LATERAL {BASE}.decode_records(c.payload,c.records::integer,c.start_ordinal::integer) f
        LEFT JOIN {BASE}.dictionary dp ON dp.name='phase' AND dp.id=f.phase
        LEFT JOIN {BASE}.dictionary dn ON dn.name='nominal_phase' AND dn.id=f.nominal_phase
        LEFT JOIN {BASE}.dictionary dm ON dm.name='marker' AND dm.id=f.marker
        LEFT JOIN {BASE}.dictionary du ON du.name='unique_marker' AND du.id=f.unique_marker
        JOIN {BASE}.player_map bat ON bat.key=f.batter
        LEFT JOIN {BASE}.player_map partner ON partner.key=f.non_striker
        LEFT JOIN LATERAL (SELECT jsonb_agg(jsonb_build_object('event_key',e.event_key,
            'player_out_id',e.player_out_id,'kind',e.kind,'quantity',e.quantity,
            'team_wickets',e.team_wickets,'batter_dismissals',e.batter_dismissals,
            'bowler_wickets',e.bowler_wickets,'fielders',e.fielders) ORDER BY e.event_key) AS items
            FROM {BASE}.dismissal_events e WHERE e.match_id=c.match_id
            AND e.innings_number=c.innings_number AND e.delivery_ordinal=f.ordinal AND NOT e.source_only) events ON true
        WHERE f.bowler=%s AND (f.batter=%s OR EXISTS(
            SELECT 1 FROM {BASE}.dismissal_events e WHERE e.match_id=c.match_id
            AND e.innings_number=c.innings_number AND e.delivery_ordinal=f.ordinal
            AND e.player_out_id=%s AND NOT e.source_only))
        AND (%s IS NULL OR f.phase=%s)
        ORDER BY c.date DESC NULLS LAST,c.match_id DESC,c.innings_number DESC,f.ordinal DESC''',
        (batter_key,bowler_key,batter_id,bowler_key,*params,bowler_key,batter_key,batter_id,phase_code,phase_code))


def read_matchup(session:ProofSession,batter_id:str,bowler_id:str,filters:PlayerFilters,phase:str|None=None)->dict:
    authority=assert_matchup_read(session);_validate_filters(session,filters)
    batter=_resolve(session,batter_id);bowler=_resolve(session,bowler_id)
    keys=_query(session,f'SELECT player_id,key FROM {BASE}.player_map WHERE player_id IN(%s,%s)',(batter['player_id'],bowler['player_id']))
    keys={row['player_id']:row['key'] for row in keys};bk=keys[batter['player_id']];wk=keys[bowler['player_id']]
    phase_code=None
    if phase is not None:
        if type(phase) is not str or not phase:raise InvalidPlayerFilters('Nonempty exact phase text required')
        phases=_query(session,f"SELECT id FROM {BASE}.dictionary WHERE name='phase' AND value=%s",(phase,))
        if not phases:raise InvalidPlayerFilters('Unknown source phase selector')
        phase_code=phases[0]['id']
    where,params=_where(filters)
    rows=_query(session,f'''SELECT v.*,dp.value AS phase,s.record_class,s.playing_format,s.balls_per_group,
        s.competition_id,s.competition_label FROM {SCHEMA}.batter_vs_bowler v
        JOIN {PLAYER_SCHEMA}.scopes s ON s.key=v.scope_key JOIN {BASE}.dictionary dp ON dp.name='phase' AND dp.id=v.phase_code
        WHERE v.batter_key=%s AND v.bowler_key=%s AND {where} AND (%s IS NULL OR v.phase_code=%s)
        ORDER BY v.year DESC NULLS LAST,v.scope_key,v.phase_code''',(bk,wk,*params,phase_code,phase_code))
    totals=_rates({key:sum(r[key] for r in rows) for key in COUNTERS})
    rows=[{**row,**_rates({key:row[key] for key in COUNTERS})} for row in rows]
    details=_details(session,bk,wk,batter['player_id'],filters,phase_code) if rows else []
    venues={};dismissals={};recent=[]
    for delivery in details:
        counts=venues.setdefault(delivery['venue'],dict.fromkeys(COUNTERS,0))
        if delivery['batter']==bk:
            runs=delivery['scored_batter'];nonwide=delivery['observed_wides']==0
            counts['runs']+=runs;counts['balls_faced']+=int(nonwide)
            counts['legal_balls']+=int(nonwide and delivery['observed_noballs']==0)
            for name,target in [('zero_run_balls',0),('four_run_balls',4),('six_run_balls',6)]:counts[name]+=int(nonwide and runs==target)
        for event in delivery['dismissal_events']:
            if event['player_out_id']!=batter['player_id']:continue
            counts['dismissals']+=event['bowler_wickets']
            aggregate=dismissals.setdefault(event['kind'],dict.fromkeys(('quantity','team_wickets','batter_dismissals','bowler_wickets'),0))
            for key in aggregate:aggregate[key]+=event[key]
        if len(recent)<10:
            row={key:delivery[key] for key in ('match_id','innings_number','ordinal','date','venue','record_class','playing_format',
                'balls_per_group','competition_id','competition_label','phase','nominal_phase','source_marker','source_unique_marker',
                'source_group_number','non_striker_id','dismissal_events')}
            row.update(batter_id=delivery['delivery_batter_id'],bowler_id=bowler['player_id'],
                group_number=delivery['group'],event_ball_number=delivery['ball'],
                legal=delivery['observed_wides']==delivery['observed_noballs']==0,
                legal_before=delivery['before'],legal_after=delivery['after'],pair_striker_activity=delivery['batter']==bk)
            for kind in ('observed','scored'):
                values={part:delivery[f'{kind}_{part}'] for part in ('batter','wides','noballs','byes','legbyes','penalties')}
                row.update({f'{part}_{kind}':value for part,value in values.items()})
                row.update({f'extras_{kind}':sum(values.values())-values['batter'],f'total_{kind}':sum(values.values())})
            row['bowler_charge']=delivery['scored_batter']+delivery['scored_wides']+delivery['scored_noballs']
            recent.append(row)
    venue_rows=[{'venue':venue,**_rates(counts)} for venue,counts in venues.items()]
    venue_rows.sort(key=lambda row:(-row['runs'],row['venue'] is None,row['venue'] or ''))
    return {'batter':batter,'bowler':bowler,'filters':{**asdict(filters),'phase':phase},'source_run_id':authority['run_id'],
        'availability':'observed_pair' if rows else 'no_observed_pair','totals':totals,'rows':rows,'venues':venue_rows,
        'dismissals':[{'kind':kind,**counts} for kind,counts in sorted(dismissals.items())],'recent_deliveries':recent,
        'coverage':{'dataset_matches':authority['matches'],'held_matches':authority['held_count'],
            'full_admitted_snapshot_ready':authority['ready'],'eligibility':'source_standard_stats',
            'four_six_basis':'scored_batter_run_nonwide_deliveries','zero_run_basis':'zero_scored_batter_run_nonwide_deliveries',
            'dismissal_basis':'recorded_actual_out_actor_bowler_wicket_credits','recent_basis':'striker_pair_or_actual_out_event_delivery'}}
