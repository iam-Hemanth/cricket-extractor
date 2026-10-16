"""Exact player totals and comparisons over guarded, standard-stat summaries."""
from dataclasses import asdict,dataclass
from decimal import Decimal

from psycopg2.extras import RealDictCursor
from db.player_summary import SCHEMA,BASE,assert_player_read


class InvalidPlayerFilters(ValueError):
    """Invalid explicit selector, distinct from source/read integrity failures."""


@dataclass(frozen=True)
class PlayerFilters:
    record_class:str|None=None
    playing_format:str|None=None
    competition_id:int|None=None
    year:int|None=None

    def __post_init__(self):
        for value in (self.record_class,self.playing_format):
            if value is not None and (type(value) is not str or not value):raise InvalidPlayerFilters('Nonempty native scope text required')
        for value,high in ((self.year,9999),(self.competition_id,9223372036854775807)):
            if value is not None and (type(value) is not int or not 1<=value<=high):raise InvalidPlayerFilters('Positive native selector integer required')


def _query(session,statement,params=()):
    with session.connection.cursor(cursor_factory=RealDictCursor) as c:
        c.execute(statement,params)
        return [{k:float(v) if isinstance(v,Decimal) else v for k,v in row.items()} for row in c.fetchall()]


def _where(filters,alias='s',year='v.year'):
    return f'(%s IS NULL OR {alias}.record_class=%s) AND (%s IS NULL OR {alias}.playing_format=%s) AND (%s IS NULL OR {alias}.competition_id=%s) AND (%s IS NULL OR {year}=%s)',tuple(x for x in (filters.record_class,filters.playing_format,filters.competition_id,filters.year) for _ in range(2))


def _validate_filters(session,filters):
    if not isinstance(filters,PlayerFilters):raise InvalidPlayerFilters('Validated PlayerFilters required')
    for column,value in [('record_class',filters.record_class),('playing_format',filters.playing_format)]:
        if value is not None and not _query(session,f'SELECT 1 FROM {SCHEMA}.scopes WHERE {column}=%s LIMIT 1',(value,)):raise InvalidPlayerFilters('Unknown source scope selector')


def _resolve(session,player_id):
    rows=_query(session,f'''SELECT player_id,name FROM {BASE}.players WHERE player_id=%s
        UNION SELECT p.player_id,p.name FROM {BASE}.player_aliases a JOIN {BASE}.players p USING(player_id) WHERE a.source='cricsheet' AND a.source_id=%s''',(player_id,player_id))
    if not rows:raise LookupError('Unknown player')
    if len(rows)!=1:raise ValueError('Ambiguous player identity')
    return rows[0]


def read_player(session,player_id:str,filters:PlayerFilters):
    authority=assert_player_read(session);_validate_filters(session,filters);player=_resolve(session,player_id)
    pid=player['player_id'];key=_query(session,f'SELECT key FROM {BASE}.player_map WHERE player_id=%s',(pid,))[0]['key']
    where,params=_where(filters)
    scopes='s.record_class,s.playing_format,s.balls_per_group,s.competition_id,s.competition_label'
    batrows=_query(session,f'''SELECT v.*,{scopes},ROUND(v.runs::numeric/NULLIF(v.dismissals,0),2) AS average,
        ROUND(v.runs*100.0/NULLIF(v.balls_faced,0),2) AS strike_rate,
        COALESCE(highest_not_out_score=highest_score,false) AS highest_score_not_out
        FROM {SCHEMA}.player_batting v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key
        WHERE v.player_key=%s AND {where} ORDER BY v.year DESC NULLS LAST,v.scope_key''',(key,*params))
    bowlrows=_query(session,f'''SELECT v.*,{scopes},ROUND(v.runs_conceded*6.0/NULLIF(v.legal_balls,0),2) AS economy,
        ROUND(v.runs_conceded::numeric/NULLIF(v.wickets,0),2) AS bowling_average,
        ROUND(v.legal_balls::numeric/NULLIF(v.wickets,0),2) AS bowling_strike_rate,
        m.match_id AS best_match_id,m.date::text AS best_date
        FROM {SCHEMA}.player_bowling v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key
        JOIN {BASE}.match_map mp ON mp.key=v.best_match_key JOIN {BASE}.matches m USING(match_id)
        WHERE v.player_key=%s AND {where} ORDER BY v.year DESC NULLS LAST,v.scope_key''',(key,*params))
    bat=_query(session,f'''SELECT COALESCE(SUM(runs),0)::bigint AS runs,COALESCE(SUM(balls_faced),0)::bigint AS balls_faced,
        COALESCE(SUM(dismissals),0)::bigint AS dismissals,COALESCE(SUM(observed_innings),0)::bigint AS observed_innings,
        COALESCE(SUM(fifties),0)::bigint AS fifties,COALESCE(SUM(hundreds),0)::bigint AS hundreds,COALESCE(SUM(ducks),0)::bigint AS ducks,
        MAX(highest_score) AS highest_score,MAX(highest_not_out_score) AS highest_not_out_score,
        ROUND(SUM(runs)::numeric/NULLIF(SUM(dismissals),0),2) AS average,
        ROUND(SUM(runs)*100.0/NULLIF(SUM(balls_faced),0),2) AS strike_rate
        FROM {SCHEMA}.player_batting v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key WHERE v.player_key=%s AND {where}''',(key,*params))[0]
    bat['highest_score_not_out']=None if bat['highest_score'] is None else bat['highest_not_out_score']==bat['highest_score']
    bowl=_query(session,f'''SELECT COALESCE(SUM(runs_conceded),0)::bigint AS runs_conceded,COALESCE(SUM(legal_balls),0)::bigint AS legal_balls,
        COALESCE(SUM(wickets),0)::bigint AS wickets,COALESCE(SUM(observed_innings),0)::bigint AS observed_innings,
        COALESCE(SUM(five_wicket_innings),0)::bigint AS five_wicket_innings,COALESCE(SUM(ten_wicket_matches),0)::bigint AS ten_wicket_matches,
        ROUND(SUM(runs_conceded)*6.0/NULLIF(SUM(legal_balls),0),2) AS economy,
        ROUND(SUM(runs_conceded)::numeric/NULLIF(SUM(wickets),0),2) AS bowling_average,
        ROUND(SUM(legal_balls)::numeric/NULLIF(SUM(wickets),0),2) AS bowling_strike_rate
        FROM {SCHEMA}.player_bowling v JOIN {SCHEMA}.scopes s ON s.key=v.scope_key WHERE v.player_key=%s AND {where}''',(key,*params))[0]
    for role,totals in ((1,bat),(2,bowl)):
        counts=_query(session,f'''SELECT COUNT(DISTINCT v.match_key) AS observed_matches FROM {SCHEMA}.actor_rows v
            JOIN {SCHEMA}.scopes s ON s.key=v.scope_key WHERE v.role=%s AND v.player_key=%s AND v.standard_stats AND {where}''',(role,key,*params))[0]
        totals['observed_matches']=counts['observed_matches']
    best=None
    if bowlrows:
        best=min(bowlrows,key=lambda r:(-r['best_wickets'],r['best_runs'],r['best_date'] is None,
            -(int(r['best_date'].replace('-','')) if r['best_date'] else 0),r['best_match_id'],r['best_innings_number']))
        best={k:best[k] for k in ('best_wickets','best_runs','best_legal_balls','best_match_id','best_innings_number','best_date')}
    bowl['best_figures']=best
    coverage_where,coverage_params=_where(filters,year='ms.year')
    unknown=_query(session,f'''SELECT COALESCE(SUM(i.source_only_timed_out),0)::bigint AS quantity
        FROM {BASE}.participant_records p JOIN {SCHEMA}.match_scope ms USING(match_id)
        JOIN {SCHEMA}.scopes s ON s.key=ms.scope_key JOIN {BASE}.innings i USING(match_id)
        WHERE p.player_id=%s AND i.standard_stats AND {coverage_where}''',(pid,*coverage_params))[0]['quantity']
    return {**player,'filters':asdict(filters),'source_run_id':authority['run_id'],
        'coverage':{'dataset_matches':authority['matches'],'held_matches':authority['held_count'],
            'full_admitted_snapshot_ready':authority['ready'],'appearance_basis':'observed_activity_only',
            'eligibility':'source_standard_stats','economy_basis_balls':6,
            'unattributed_team_wickets_in_listed_matches':unknown},
        'batting':{'availability':'observed_standard_innings' if batrows else 'no_observed_standard_innings','totals':bat,'rows':batrows},
        'bowling':{'availability':'observed_standard_innings' if bowlrows else 'no_observed_standard_innings','totals':bowl,'rows':bowlrows}}


def compare_players(session,first_id:str,second_id:str,filters:PlayerFilters):
    first=read_player(session,first_id,filters);second=read_player(session,second_id,filters)
    if first['source_run_id']!=second['source_run_id']:raise ValueError('Comparison source generations differ')
    return {'first':first,'second':second,'source_run_id':first['source_run_id']}
