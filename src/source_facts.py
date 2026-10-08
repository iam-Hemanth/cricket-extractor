from decimal import Decimal, InvalidOperation

"""Keep scorecard-only facts apart from ball-level attribution."""

def dismissal_text(entry):
    text=entry.get('dismissalText') or ''
    return str(text.get('short') or '' if isinstance(text,dict) else text).strip().lower()


def scorecard_snapshot(source):
    extras=source.get('extras') if isinstance(source.get('extras'),dict) else {}
    snap={k:source.get(k) for k in ('runs','wickets','balls','penalties')}
    snap.update({k:source.get(k,extras.get(k)) for k in ('wides','noballs','byes','legbyes')})
    snap['timed_out']=sum(dismissal_text(p)=='timed out' for p in source.get('inningBatsmen') or [])
    return snap


def innings_penalty(deliveries,source):
    """An explicit unallocated penalty needs complete source totals/ball coverage."""
    declared=source.get('penalties')
    covered=sum(d['extras'].get('penalty',0) for d in deliveries)
    amount=0;status='not_reported'
    if declared is not None and (type(declared) is not int or declared<0):
        return {'innings_penalty_runs':0,'penalty_adjustment_status':'needs_review'}
    if declared is not None:
        status='none'
        remaining=declared-covered
        if remaining<0:
            status='needs_review'
        elif remaining:
            legal=sum(d['is_legal'] for d in deliveries)
            runs=sum(d['runs']['total'] for d in deliveries)
            if source.get('balls')==legal and source.get('runs')==runs+remaining:
                amount=remaining;status='source_backed'
            else:status='needs_review'
    return {'innings_penalty_runs':amount,'penalty_adjustment_status':status}


def canonical_marker(source_actual, over_number, ball_number, unique_marker):
    try:
        source=Decimal(source_actual)
        if (source.is_finite() and source==source.to_integral_value() and source>=0
                and type(over_number) is int and over_number==int(source)+1
                and ball_number==1 and Decimal(str(unique_marker))==source+Decimal('0.01')):
            return f'{int(source)}.1'
    except (InvalidOperation,ValueError,TypeError):
        pass
    return source_actual


def source_only_timed_out(deliveries,scorecard):
    reported=scorecard.get('timed_out') or 0
    recorded=sum(str(w.get('kind') or '').strip().lower()=='timed out'
                 for d in deliveries for w in d.get('wickets') or [])
    return max(0,reported-recorded)
