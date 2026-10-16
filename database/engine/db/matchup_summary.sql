CREATE SCHEMA cricket_matchup_read_v1;
SET search_path=cricket_matchup_read_v1,pg_catalog;
CREATE TABLE read_environment(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),ddl_sha256 TEXT NOT NULL,source_run_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,codebook_sha256 TEXT NOT NULL,cluster_run_id TEXT NOT NULL);
CREATE FUNCTION require_smallint_bounds(valid BOOLEAN) RETURNS BOOLEAN
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
 IF NOT valid THEN
 RAISE EXCEPTION 'Matchup SMALLINT bounds exceeded; publish a wider summary version' USING ERRCODE='22003';
 END IF;
 RETURN true;
END $$;
CREATE MATERIALIZED VIEW batter_vs_bowler AS
WITH activity AS (
 SELECT f.batter AS batter_key,f.bowler AS bowler_key,ms.scope_key,ms.year,f.phase AS phase_code,
 SUM(f.scored_batter) AS runs,COUNT(*) FILTER(WHERE f.observed_wides=0) AS balls_faced,
 COUNT(*) FILTER(WHERE f.observed_wides=0 AND f.observed_noballs=0) AS legal_balls,
 COUNT(*) FILTER(WHERE f.observed_wides=0 AND f.scored_batter=0) AS zero_run_balls,
 COUNT(*) FILTER(WHERE f.observed_wides=0 AND f.scored_batter=4) AS four_run_balls,
 COUNT(*) FILTER(WHERE f.observed_wides=0 AND f.scored_batter=6) AS six_run_balls
 FROM cricket_compact_v1.facts f JOIN cricket_compact_v1.innings i USING(match_key,innings_number)
 JOIN cricket_player_read_v1.match_scope ms USING(match_key)
 WHERE i.standard_stats GROUP BY f.batter,f.bowler,ms.scope_key,ms.year,f.phase
), credits AS (
 SELECT p.key AS batter_key,f.bowler AS bowler_key,ms.scope_key,ms.year,f.phase AS phase_code,
 SUM(e.bowler_wickets) AS dismissals
 FROM cricket_compact_v1.dismissal_events e
 JOIN cricket_compact_v1.player_map p ON p.player_id=e.player_out_id
 JOIN cricket_player_read_v1.match_scope ms USING(match_id)
 JOIN cricket_compact_v1.innings i ON i.match_id=e.match_id AND i.innings_number=e.innings_number
 JOIN cricket_compact_v1.blocks b ON b.match_key=ms.match_key AND b.innings_number=e.innings_number
 AND e.delivery_ordinal>=b.start_ordinal AND e.delivery_ordinal<b.start_ordinal::integer+b.records
 CROSS JOIN LATERAL cricket_compact_v1.decode_records(b.payload,b.records::integer,b.start_ordinal::integer) f
 WHERE NOT e.source_only AND i.standard_stats AND f.ordinal=e.delivery_ordinal
 GROUP BY p.key,f.bowler,ms.scope_key,ms.year,f.phase
), buckets AS MATERIALIZED (
SELECT COALESCE(a.batter_key,c.batter_key) AS batter_key,COALESCE(a.bowler_key,c.bowler_key) AS bowler_key,
 COALESCE(a.scope_key,c.scope_key) AS scope_key,COALESCE(a.year,c.year) AS year,
 COALESCE(a.phase_code,c.phase_code) AS phase_code,COALESCE(a.runs,0) AS runs,
 COALESCE(a.balls_faced,0) AS balls_faced,COALESCE(a.legal_balls,0) AS legal_balls,
 COALESCE(a.zero_run_balls,0) AS zero_run_balls,COALESCE(a.four_run_balls,0) AS four_run_balls,
 COALESCE(a.six_run_balls,0) AS six_run_balls,COALESCE(c.dismissals,0) AS dismissals
FROM activity a FULL JOIN credits c ON c.batter_key=a.batter_key AND c.bowler_key=a.bowler_key
 AND c.scope_key=a.scope_key AND c.year IS NOT DISTINCT FROM a.year AND c.phase_code=a.phase_code
), bounds AS MATERIALIZED (
 SELECT require_smallint_bounds(NOT EXISTS(SELECT 1 FROM buckets WHERE
 batter_key NOT BETWEEN 1 AND 32767 OR bowler_key NOT BETWEEN 1 AND 32767
 OR scope_key NOT BETWEEN 1 AND 32767 OR phase_code NOT BETWEEN 1 AND 32767
 OR (year IS NOT NULL AND year NOT BETWEEN 1 AND 32767)
 OR runs NOT BETWEEN 0 AND 32767 OR balls_faced NOT BETWEEN 0 AND 32767
 OR legal_balls NOT BETWEEN 0 AND 32767 OR zero_run_balls NOT BETWEEN 0 AND 32767
 OR four_run_balls NOT BETWEEN 0 AND 32767 OR six_run_balls NOT BETWEEN 0 AND 32767
 OR dismissals NOT BETWEEN 0 AND 32767)) AS valid
)
SELECT batter_key::smallint AS batter_key,bowler_key::smallint AS bowler_key,scope_key::smallint AS scope_key,
 year::smallint AS year,phase_code::smallint AS phase_code,runs::smallint AS runs,
 balls_faced::smallint AS balls_faced,legal_balls::smallint AS legal_balls,
 zero_run_balls::smallint AS zero_run_balls,four_run_balls::smallint AS four_run_balls,
 six_run_balls::smallint AS six_run_balls,dismissals::smallint AS dismissals FROM buckets CROSS JOIN bounds WHERE bounds.valid;
CREATE UNIQUE INDEX batter_vs_bowler_key ON batter_vs_bowler(batter_key,bowler_key,scope_key,year,phase_code) NULLS NOT DISTINCT WITH(fillfactor=100);
ANALYZE batter_vs_bowler;
