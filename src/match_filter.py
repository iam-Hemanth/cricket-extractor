"""
ingestion/match_filter.py
Shared match filter used by both ingest_all.py and sync.py.
Controls which matches are allowed into the database.
"""

# ── Permanently blocked match IDs ───────────────────────────
# Add Cricsheet match IDs here for matches that should never
# be ingested (e.g. abandoned due to external circumstances,
# where partial data exists but the match was not completed).
_BLOCKED_MATCH_IDS = {
    "1473495",  # IPL 2025: PBKS vs DC — abandoned mid-game (war situation), not an official result
}

_FULL_MEMBERS = {
    'India', 'Australia', 'England', 'Pakistan',
    'South Africa', 'New Zealand', 'West Indies', 'Sri Lanka'
}

_ALLOWED_T20_LEAGUES = {
    'Indian Premier League',
    'SA20',
    'The Hundred Men\'s Competition',
    'International League T20',
    'Major League Cricket'
}

_ICC_EVENT_PATTERNS = [
    'ICC Cricket World Cup',
    "ICC Men's T20 World Cup",
    'ICC World Twenty20',
    'ICC Champions Trophy',
    'ICC World Test Championship'
]

_ASSOCIATE_EXCLUDE_PATTERNS = [
    "ICC Men's Cricket World Cup League 2",
    'ICC CWC Qualifier',
    'ICC T20 World Cup Qualifier'
]

def should_ingest_match(info: dict, match_id: str = "") -> tuple[bool, str]:
    """
    Returns (True, '') to ingest or (False, reason) to skip.

    Keep rules:
    - At least one team must be a full member OR
      competition is an ICC flagship event OR
      Asia Cup (not qualifier) OR allowed T20 league
    Always drop:
    - Blocked match IDs (see _BLOCKED_MATCH_IDS above)
    - MDM, ODM formats
    - Qualifiers and regional tournaments
    - Pre-2011 Tests, pre-2007 ODIs
    - Matches where neither team is a full member and
      competition is not an allowed event
    """
    # Always drop explicitly blocked match IDs
    if match_id in _BLOCKED_MATCH_IDS:
        return False, f"blocked match ID: {match_id}"

    teams = info.get('teams', [])
    fmt = info.get('match_type', '')
    event = info.get('event') or {}
    competition = event.get('name', '') or ''
    comp_lower = competition.lower()

    # Always drop MDM and ODM
    if fmt in ('MDM', 'ODM'):
        return False, f"excluded format: {fmt}"

    # Always drop associate-only ICC events
    if any(p.lower() in comp_lower for p in _ASSOCIATE_EXCLUDE_PATTERNS):
        return False, f"associate ICC event: {competition}"

    # Always drop qualifiers and regional tournaments
    if 'qualifier' in comp_lower:
        return False, f"qualifier event: {competition}"
    if 'region' in comp_lower:
        return False, f"regional event: {competition}"

    # Always drop Asia Cup qualifiers
    if 'asia cup' in comp_lower and 'qualifier' in comp_lower:
        return False, f"Asia Cup qualifier: {competition}"

    # Always drop pre-2011 Tests
    if fmt == 'Test':
        dates = info.get('dates', [])
        if dates and dates[0] < '2011-01-01':
            return False, f"pre-2011 Test: {dates[0]}"

    # Always drop pre-2007 ODIs
    if fmt == 'ODI':
        dates = info.get('dates', [])
        if dates and dates[0] < '2007-01-01':
            return False, f"pre-2007 ODI: {dates[0]}"

    # Keep Asia Cup main event
    if 'asia cup' in comp_lower:
        return True, ''

    # Keep ICC flagship events
    if any(p.lower() in comp_lower for p in _ICC_EVENT_PATTERNS):
        return True, ''

    # Keep allowed T20 leagues
    if competition in _ALLOWED_T20_LEAGUES:
        return True, ''

    # Keep if at least one team is a full member
    if any(t in _FULL_MEMBERS for t in teams):
        return True, ''

    # Drop everything else
    return False, (
        f"no full member + not allowed competition: "
        f"fmt={fmt}, competition={competition}, teams={teams}"
    )
