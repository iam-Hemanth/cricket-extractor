"""Hundred core rules and clocks, with nominal restrictions kept attributable."""
import copy
import json
from functools import lru_cache
from pathlib import Path

COMPETITIONS={"The Hundred Men's Competition":'male',"The Hundred Women's Competition":'female'}
PHASE_POLICY='hundred_allocated_last_quarter_v1'


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((Path(__file__).parents[1]/'schema/hundred_rules.json').read_text())


def is_hundred(raw_format, competition):
    return str(raw_format or '').upper() in {'HUNDRED','HUNDRED_BALL'} or competition in COMPETITIONS


def ruleset(season, start_date, competition):
    year=str(start_date or '')[:4]
    selected=str(season) if season is not None else year
    if competition not in COMPETITIONS or (year and selected!=year):
        return None
    return copy.deepcopy(_catalog()['rulesets'].get(selected))


def match_metadata(match_info, competition, dates):
    pack=ruleset(match_info.get('season'),dates[0] if dates else None,competition)
    return {'playing_format':'hundred_ball','source_classification':{
                'format':match_info.get('format'),'general_class_id':match_info.get('generalClassId'),
                'international_class_id':match_info.get('internationalClassId')},
            'ruleset_id':pack['id'] if pack else None,
            'ruleset_status':'verified_core_rules' if pack else 'unverified','ruleset':pack,
            'display_unit':'balls','economy_basis_balls':6,'phase_policy_id':PHASE_POLICY,
            'gender':COMPETITIONS.get(competition,'unknown')}


def positive_integer(value):
    return value if type(value) is int and value>0 else None


def innings_metadata(source, balls, pack, special):
    """Final quota is evidence of allocation, never an interruption timeline."""
    reported=source.get('totalBalls')
    quota=positive_integer(reported)
    issues=[]
    source_limits=sorted({positive_integer((b.get('over') or {}).get('ballLimit'))
                          for b in balls} - {None})
    if reported is not None and quota is None:
        issues.append('invalid_source_allocation')
    if quota is None:
        issues.append('missing_source_allocation')
    if any(limit!=quota for limit in source_limits):
        issues.append('conflicting_source_allocations')
    kind='super_five' if special else 'main'
    if special and quota!=5:
        issues.append('unverified_special_allocation')
    nominal_pp=(_catalog()['reduced_powerplay_balls'].get(str(quota//5))
                if quota and quota%5==0 and pack and not special else None)
    if not special and quota!=100:
        issues.append('restriction_timeline_unavailable')
    if pack is None:
        issues.append('unverified_season_rules')
    if not special and quota and (quota%5 or quota<25 or quota>100):
        issues.append('unsupported_main_allocation')
    nominal_death=(quota-(quota+3)//4+1 if nominal_pp is not None else None)
    return {'innings_kind':kind,'include_in_standard_stats':not special,
            'allocated_legal_balls':quota,'source_legal_balls':source.get('balls'),'allocation_source':'scorecard.totalBalls' if quota else 'unknown',
            'source_allocations':{'scorecard_total_balls':reported,'commentary_ball_limits':source_limits},
            'nominal_powerplay_balls':nominal_pp,'nominal_death_start_ball':nominal_death,
            'phase_basis':'nominal_conditions' if not issues and not special else (
                'special_innings' if special and not issues else 'unknown'),
            'rule_review_issues':issues}


def nominal_phase(legal_before, innings):
    pp,death=innings['nominal_powerplay_balls'],innings['nominal_death_start_ball']
    if innings['innings_kind']=='super_five':
        return 'neutral'
    if pp is None or death is None:
        return 'unknown'
    if legal_before<pp:
        return 'powerplay'
    return 'death' if legal_before>=death-1 else 'middle'


def delivery_phase(legal_before, innings):
    if innings['innings_kind']=='super_five':
        return 'neutral'
    return nominal_phase(legal_before,innings) if innings['phase_basis']=='nominal_conditions' else 'unknown'
