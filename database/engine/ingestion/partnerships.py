"""Pure observed partnership stints over canonical numeric delivery actors."""

_RETIREMENTS=frozenset(('retired hurt','retired not out','retired out'))
_PARTS=('batter','wides','noballs','byes','legbyes','penalties')


def _identity(row):
    name='match_id' if 'match_id' in row else 'match_key'
    return name,row[name],row['innings_number']


def _actor(row,name):
    value=row.get(name+'_key',row.get(name))
    return value if type(value) is int and value>0 else None


def segment_partnerships(deliveries,events=())->list[dict]:
    """Allocate scored delivery runs only; missing partners and penalties stay out.

    Inputs use facts' batter/non_striker keys (or explicit *_key aliases).
    Events are delivery linked and carry a canonical numeric player_out_key,
    player_out, or numeric player_out_id. Each boundary applies AFTER its delivery.
    """
    boundaries={}
    for event in events:
        ordinal=event.get('delivery_ordinal')
        if event.get('source_only') or type(ordinal) is not int or ordinal<=0:continue
        actor=event.get('player_out_key',event.get('player_out',event.get('player_out_id')))
        if type(actor) is not int or actor<=0:continue
        if event.get('team_wickets',0)>0 or event.get('kind') in _RETIREMENTS:
            boundaries.setdefault((*_identity(event),ordinal),set()).add(actor)
    ordered=sorted(deliveries,key=lambda row:(*_identity(row),row['ordinal']))
    result=[];current=None;previous=None;ended=False;stint_number=0
    for row in ordered:
        identity=_identity(row);ordinal=row['ordinal']
        batter=_actor(row,'batter');partner=_actor(row,'non_striker')
        pair=tuple(sorted((batter,partner))) if batter and partner and batter!=partner else None
        new_innings=previous is None or identity!=previous[0]
        if new_innings:stint_number=0
        continuous=(previous is not None and identity==previous[0] and ordinal==previous[1]+1)
        if pair is None:
            current=None
        else:
            if current is None or not continuous or ended or pair!=(current['pair1'],current['pair2']):
                stint_number+=1
                current={identity[0]:identity[1],'innings_number':identity[2],
                    'pair1':pair[0],'pair2':pair[1],'stint_number':stint_number,
                    'start_ordinal':ordinal,'end_ordinal':ordinal,
                    'total_runs':0,'delivered_balls':0,'legal_balls':0}
                result.append(current)
            runs=row.get('total_scored')
            if runs is None:
                runs=sum(row.get('scored_'+part,row.get(part+'_scored',0)) for part in _PARTS)
            legal=row.get('legal')
            if legal is None:
                legal=(row.get('observed_wides',row.get('wides_observed',0))==0
                       and row.get('observed_noballs',row.get('noballs_observed',0))==0)
            current['end_ordinal']=ordinal
            current['total_runs']+=runs
            current['delivered_balls']+=1
            current['legal_balls']+=int(legal)
        ended=bool(pair and boundaries.get((*identity,ordinal),set()).intersection(pair))
        previous=identity,ordinal
    return result
