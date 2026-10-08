"""Curated product admission; priority is not ICC membership or record class."""
from src.classification import classify, roster_gender_values

POLICY_ID='curated_launch_20261008_v1'
_PRIORITY_TEAMS={'India','Australia','England','Pakistan','South Africa','New Zealand','West Indies','Sri Lanka'}
_BLOCKED_MATCH_IDS={'1473495'}
_LEAGUES={'Indian Premier League','SA20',"The Hundred Men's Competition",'Major League Cricket'}
_LEAGUE_TROPHIES={117:'IPL',987:'SA20',826:'Hundred',985:'MLC'}
_FLAGSHIP_TROPHIES={12,44,89,804}
_ASIA_TROPHIES={14,951}
_EXCLUDED_TROPHIES={946}
_ICC_EVENT_PATTERNS=('ICC Cricket World Cup',"ICC Men's T20 World Cup",'ICC World Twenty20','ICC Champions Trophy','ICC World Test Championship')
_EXCLUDED_EVENTS=("ICC Men's Cricket World Cup League 2",'ICC CWC Qualifier','ICC T20 World Cup Qualifier')


def scope_decision(info,match_id='',verified=False):
    """Three states preserve uncertainty; discovery never certifies source facts."""
    def answer(status,reason):
        return {'status':status,'reason':reason,'policy_id':POLICY_ID}
    if str(match_id) in _BLOCKED_MATCH_IDS:
        return answer('excluded','blocked_match')
    competition=info.get('competition') or '';lower=competition.lower()
    trophy=info.get('trophy_id');trophy=trophy if type(trophy) is int else None
    if trophy in _EXCLUDED_TROPHIES or 'international league t20' in lower or 'ilt20' in lower:
        return answer('excluded','ilt20_removed')
    if any(p.lower() in lower for p in _EXCLUDED_EVENTS) or 'qualifier' in lower:
        return answer('excluded','qualifier_excluded')
    if 'region' in lower:
        return answer('excluded','regional_excluded')
    if verified:
        if info.get('state') is not None and info['state']!='POST':
            return answer('held','uncompleted_source')
        if info.get('classification_status')!='source_codes':
            return answer('held','source_classification_unknown')
        if info.get('record_stats_eligible') is not True:
            return answer('excluded','outside_senior_record_classes')
        if info.get('gender')=='female':
            return answer('excluded','outside_mens_launch')
        if info.get('gender')!='male':
            return answer('held','source_gender_unknown')
    teams=info.get('teams') or []
    if len(teams)!=2 or any(not isinstance(t,str) or not t.strip() for t in teams):
        return answer('held','source_team_identity_unknown')
    record=info.get('record_class')
    fmt=str(info.get('format_bucket') or info.get('match_type') or '').upper()
    if verified and record not in {'test','odi','t20i','t20'}:
        return answer('excluded','outside_senior_record_classes')
    if fmt in {'MDM','ODM','FC','FIRST-CLASS','LISTA','LIST A','OTHERMDM','OTHERODM','OTHERT20','UNKNOWN'}:
        return answer('excluded','outside_senior_record_classes')
    dates=info.get('dates') or []
    if fmt in {'TEST','ODI'}:
        if not dates or not isinstance(dates[0],str) or len(dates[0])<10:
            return answer('held','cutoff_date_unknown')
        cutoff='2011-01-01' if fmt=='TEST' else '2007-01-01'
        if dates[0][:10]<cutoff:
            return answer('excluded','before_format_cutoff')
    allowed_league=trophy in _LEAGUE_TROPHIES or competition in _LEAGUES
    if verified and record=='t20':
        return answer('selected','allowed_league') if allowed_league else answer('excluded','domestic_league_not_whitelisted')
    # Provisional discovery labels can overlap; actual source admission runs again.
    if allowed_league:
        return answer('selected','allowed_league')
    if trophy in _ASIA_TROPHIES or 'asia cup' in lower:
        return answer('selected','allowed_asia_event')
    if trophy in _FLAGSHIP_TROPHIES or any(p.lower() in lower for p in _ICC_EVENT_PATTERNS):
        return answer('selected','allowed_flagship_event')
    if any(t in _PRIORITY_TEAMS for t in teams):
        return answer('selected','priority_opponent')
    return answer('excluded','no_priority_opponent_or_allowed_event')


def source_scope_facts(details):
    info=details.get('match') or {};meta=classify(info,roster_gender_values(details))
    return dict(meta,teams=[(t.get('team') or {}).get('name') for t in info.get('teams') or []],
        dates=[str(info.get('startDate') or '').split('T')[0]] if info.get('startDate') else [],
        trophy_id=meta['source_competition_trophy_id'],competition=(info.get('series') or {}).get('name') or (info.get('event') or {}).get('name'),state=info.get('state'))


def source_scope_decision(details,match_id=''):
    return scope_decision(source_scope_facts(details),match_id,verified=True)


def should_ingest_match(info,match_id=''):
    """Compatibility interface for discovery candidates, not source certification."""
    event=info.get('event') or {}
    facts=dict(info,competition=event.get('name') or '',trophy_id=event.get('trophy_id'))
    decision=scope_decision(facts,match_id,verified=False)
    return decision['status']=='selected',decision['reason']
