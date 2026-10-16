CREATE SCHEMA cricket_partnership_read_v1;
SET search_path=cricket_partnership_read_v1,pg_catalog;
CREATE TABLE read_environment(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),ddl_sha256 TEXT NOT NULL,source_run_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,codebook_sha256 TEXT NOT NULL,cluster_run_id TEXT NOT NULL);
CREATE MATERIALIZED VIEW partnerships AS
WITH events AS (
 SELECT mp.key AS match_key,e.innings_number,e.delivery_ordinal,ARRAY_AGG(DISTINCT p.key) AS player_keys
 FROM cricket_compact_v1.dismissal_events e
 JOIN cricket_compact_v1.match_map mp USING(match_id)
 JOIN cricket_compact_v1.player_map p ON p.player_id=e.player_out_id
 WHERE NOT e.source_only AND e.delivery_ordinal IS NOT NULL
 AND (e.team_wickets>0 OR e.kind IN('retired hurt','retired not out','retired out'))
 GROUP BY mp.key,e.innings_number,e.delivery_ordinal
), deliveries AS (
 SELECT f.match_key,f.innings_number,f.ordinal,
 CASE WHEN f.non_striker>0 AND f.batter<>f.non_striker THEN LEAST(f.batter,f.non_striker) END AS pair1,
 CASE WHEN f.non_striker>0 AND f.batter<>f.non_striker THEN GREATEST(f.batter,f.non_striker) END AS pair2,
 f.scored_batter+f.scored_wides+f.scored_noballs+f.scored_byes+f.scored_legbyes+f.scored_penalties AS runs,
 (f.observed_wides=0 AND f.observed_noballs=0) AS legal,
 COALESCE(f.batter=ANY(e.player_keys) OR f.non_striker=ANY(e.player_keys),false) AS boundary
 FROM cricket_compact_v1.facts f
 JOIN cricket_compact_v1.innings i USING(match_key,innings_number)
 LEFT JOIN events e ON e.match_key=f.match_key AND e.innings_number=f.innings_number AND e.delivery_ordinal=f.ordinal
 WHERE i.standard_stats
), edges AS (
 SELECT d.*,CASE WHEN
 LAG(pair1) OVER w IS DISTINCT FROM pair1 OR LAG(pair2) OVER w IS DISTINCT FROM pair2
 OR LAG(boundary) OVER w OR ordinal<>LAG(ordinal) OVER w+1
 OR LAG(ordinal) OVER w IS NULL THEN 1 ELSE 0 END AS new_stint
 FROM deliveries d WINDOW w AS(PARTITION BY match_key,innings_number ORDER BY ordinal)
), numbered AS (
 SELECT e.*,SUM(new_stint) OVER(PARTITION BY match_key,innings_number ORDER BY ordinal ROWS UNBOUNDED PRECEDING) AS stint
 FROM edges e
), stints AS (
 SELECT match_key,innings_number,pair1,pair2,stint,SUM(runs)::integer AS runs,
 COUNT(*)::integer AS delivered_balls,COUNT(*) FILTER(WHERE legal)::integer AS legal_balls
 FROM numbered WHERE pair1 IS NOT NULL GROUP BY match_key,innings_number,pair1,pair2,stint
), pair_innings AS (
 SELECT match_key,innings_number,pair1,pair2,COUNT(*)::integer AS observed_stints,
 SUM(runs)::integer AS total_runs,MAX(runs) AS best_stint_runs,
 SUM(delivered_balls)::integer AS delivered_balls,SUM(legal_balls)::integer AS legal_balls
 FROM stints GROUP BY match_key,innings_number,pair1,pair2
)
SELECT p.pair1::smallint AS pair1,p.pair2::smallint AS pair2,ms.scope_key,ms.year::smallint AS year,
 SUM(p.observed_stints)::integer AS observed_stints,COUNT(*)::integer AS observed_pair_innings,
 SUM(p.total_runs)::integer AS total_runs,MAX(p.best_stint_runs) AS best_stint_runs,
 MAX(p.total_runs) AS best_pair_innings_runs,SUM(p.delivered_balls)::integer AS delivered_balls,
 SUM(p.legal_balls)::integer AS legal_balls
FROM pair_innings p JOIN cricket_player_read_v1.match_scope ms USING(match_key)
GROUP BY p.pair1,p.pair2,ms.scope_key,ms.year;
CREATE UNIQUE INDEX partnerships_key ON partnerships(pair1,pair2,scope_key,year) NULLS NOT DISTINCT;
CREATE INDEX partnerships_reverse ON partnerships(pair2,pair1,scope_key,year);
ANALYZE partnerships;
