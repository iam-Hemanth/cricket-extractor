"""Source match classes, independent of physical format and collection policy."""
from src.hundred import COMPETITIONS, is_hundred

# These are HS-consumer match payload codes, not Statsguru selector IDs.
GENERAL={4:('first_class','first_class','multi_day','FC'),
         5:('list_a','list_a','one_day','ListA'),
         6:('t20','t20','t20','T20'),
         11:('other_multi_day','other_multi_day','multi_day','OtherMDM'),
         12:('other_one_day','other_one_day','one_day','OtherODM'),
         13:('other_t20','other_t20','t20','OtherT20'),
         17:('t20','t20','t20','T20')}
INTERNATIONAL={1:('test','first_class','multi_day','Test',4,'male'),
               2:('odi','list_a','one_day','ODI',5,'male'),
               3:('t20i','t20','t20','T20I',6,'male'),
               8:('test','first_class','multi_day','Test',None,'female'),
               9:('odi','list_a','one_day','ODI',None,'female'),
               10:('t20i','t20','t20','T20I',17,'female')}
RAW_PLAYING={'TEST':'multi_day','MDM':'multi_day','ODI':'one_day','ODM':'one_day',
             'T20':'t20','IT20':'t20','T20I':'t20','HUNDRED_BALL':'hundred_ball','HUNDRED':'hundred_ball'}


def gender_value(raw):
    return {'M':'male','MALE':'male','F':'female','FEMALE':'female'}.get(str(raw or '').upper())


def roster_gender_values(details):
    groups=((details.get('content') or {}).get('matchPlayers') or {}).get('teamPlayers') or []
    values=[(p.get('player') or {}).get('gender') for g in groups
            if g.get('type') in {'PLAYING','ACTIVE'} for p in g.get('players') or []]
    return sorted(set(values),key=str)


def classify(match_info, roster_values):
    competition=(match_info.get('series') or {}).get('name') or (match_info.get('event') or {}).get('name')
    raw_format=match_info.get('format');raw_play=RAW_PLAYING.get(str(raw_format or '').upper())
    general,international=match_info.get('generalClassId'),match_info.get('internationalClassId')
    # bool values must not alias integer class IDs.
    g=GENERAL.get(general) if type(general) is int else None
    i=INTERNATIONAL.get(international) if type(international) is int else None
    source={'format':raw_format,'general_class_id':general,'international_class_id':international}
    presence={'format':'format' in match_info,'general_class_id':'generalClassId' in match_info,
              'international_class_id':'internationalClassId' in match_info}
    issues=[];status='source_codes';international_status='unknown'
    record,family,playing,bucket=('unknown','unknown',raw_play or 'unknown','Unknown')
    if i:
        international_status='international'
        if general is not None and (g is None or (i[4] is not None and general!=i[4]) or (g and g[2]!=i[2])):
            issues.append('incompatible_source_classes')
        else:record,family,playing,bucket=i[:4]
    elif international is not None:
        issues.append('unsupported_international_class')
    elif not presence['international_class_id']:
        issues.append('missing_international_class_property')
    elif g:
        international_status='non_international';record,family,playing,bucket=g
    else:issues.append('unknown_general_class')
    if raw_play and playing!='unknown' and raw_play.replace('hundred_ball','t20')!=playing.replace('hundred_ball','t20'):
        issues.append('format_class_family_conflict')
    if competition in COMPETITIONS and (record!='t20' or international_status!='non_international'):
        issues.append('hundred_competition_class_conflict')
    if issues:
        record,family,bucket='unknown','unknown','Unknown';status='needs_review'
        international_status='unknown'
    if is_hundred(raw_format,competition):
        playing='hundred_ball'
    trophy=(match_info.get('series') or {}).get('trophyId')
    if bucket=='T20' and ((type(trophy) is int and trophy==117) or (trophy is None and competition=='Indian Premier League')):
        bucket='IPL'

    explicit=gender_value(match_info.get('gender'))
    class_gender=i[5] if i else ('female' if general==17 else None)
    comp_gender=COMPETITIONS.get(competition)
    roster={gender_value(v) for v in roster_values}
    strong={v for v in (explicit,class_gender,comp_gender) if v}
    gender_issues=[]
    if len(strong)>1:
        gender='unknown';gender_source='unknown';gender_issues.append('conflicting_match_gender_evidence')
    elif strong:
        gender=next(iter(strong));gender_source=('match.gender' if explicit else (
            'source_class' if class_gender else 'competition'))
        if roster_values and any(v!=gender for v in roster):gender_issues.append('roster_gender_conflict')
    elif roster_values and len(roster)==1 and None not in roster:
        gender=next(iter(roster));gender_source='playing_roster'
    else:
        gender='unknown';gender_source='unknown'
        if roster_values:gender_issues.append('incomplete_or_conflicting_roster_gender')
    return {'source_classification':source,'source_competition_trophy_id':trophy,'source_classification_presence':presence,
            'record_class':record,'statistical_family':family,'international_status':international_status,
            'playing_format':playing,'format':bucket if bucket!='IPL' else 'T20','format_bucket':bucket,
            'classification_status':status,'classification_issues':issues,
            'record_stats_eligible':status=='source_codes' and not record.startswith('other_') and record!='unknown',
            'gender':gender,'gender_source':gender_source,'gender_issues':gender_issues,
            'gender_evidence':{'match_gender':match_info.get('gender'),'playing_roster_values':roster_values}}


def source_from_metadata(meta):
    source=meta['source_classification'];presence=meta['source_classification_presence']
    result={'series':{'name':meta.get('competition'),'trophyId':meta['source_competition_trophy_id']},'gender':meta['gender_evidence']['match_gender']}
    for field,key in [('format','format'),('generalClassId','general_class_id'),('internationalClassId','international_class_id')]:
        if presence[key]:result[field]=source[key]
    return result
