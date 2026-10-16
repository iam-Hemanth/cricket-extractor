CREATE SCHEMA cricket_player_read_v1;
SET search_path=cricket_player_read_v1,pg_catalog;
CREATE TABLE read_environment(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),ddl_sha256 TEXT NOT NULL,source_run_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,codebook_sha256 TEXT NOT NULL,cluster_run_id TEXT NOT NULL);
CREATE TABLE scopes(key SMALLINT PRIMARY KEY CHECK(key>0),record_class TEXT NOT NULL,playing_format TEXT NOT NULL,balls_per_group INTEGER NOT NULL CHECK(balls_per_group>0),competition_key TEXT NOT NULL,competition_id BIGINT,competition_label TEXT,UNIQUE(record_class,playing_format,balls_per_group,competition_key));
INSERT INTO scopes
WITH labelled AS(SELECT m.*,CASE WHEN trophy_id IS NOT NULL THEN 'trophy:'||trophy_id::text WHEN competition IS NOT NULL THEN 'raw:'||competition ELSE 'none' END AS competition_key FROM cricket_compact_v1.matches m),
chosen AS(SELECT DISTINCT ON(record_class,playing_format,balls_per_group,competition_key) record_class,playing_format,balls_per_group,competition_key,trophy_id,competition FROM labelled ORDER BY record_class,playing_format,balls_per_group,competition_key,date DESC NULLS LAST,match_id)
SELECT ROW_NUMBER() OVER(ORDER BY record_class,playing_format,balls_per_group,competition_key)::smallint,record_class,playing_format,balls_per_group,competition_key,trophy_id,competition FROM chosen;
ANALYZE scopes;
CREATE FUNCTION immutable_scope() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'read scopes are immutable; publish a new coherent generation' USING ERRCODE='23514'; END $$;
CREATE TRIGGER scopes_immutable BEFORE UPDATE OR DELETE ON scopes FOR EACH ROW EXECUTE FUNCTION immutable_scope();
CREATE TRIGGER scopes_no_truncate BEFORE TRUNCATE ON scopes FOR EACH STATEMENT EXECUTE FUNCTION immutable_scope();
CREATE VIEW match_scope AS SELECT m.match_id,mp.key AS match_key,m.date,EXTRACT(YEAR FROM m.date)::integer AS year,s.key AS scope_key FROM cricket_compact_v1.matches m JOIN cricket_compact_v1.match_map mp USING(match_id) JOIN scopes s ON(s.record_class=m.record_class AND s.playing_format=m.playing_format AND s.balls_per_group=m.balls_per_group AND s.competition_key=CASE WHEN m.trophy_id IS NOT NULL THEN 'trophy:'||m.trophy_id::text WHEN m.competition IS NOT NULL THEN 'raw:'||m.competition ELSE 'none' END);
CREATE VIEW actor_rows AS SELECT a.*,ms.match_id,ms.date,ms.year,ms.scope_key,i.standard_stats FROM cricket_compact_v1.actor_innings a JOIN cricket_compact_v1.innings i USING(match_key,innings_number) JOIN match_scope ms USING(match_key);
CREATE MATERIALIZED VIEW player_batting AS
SELECT player_key,scope_key,year,COUNT(DISTINCT match_key)::integer AS observed_matches,COUNT(*)::integer AS observed_innings,SUM(runs)::integer AS runs,SUM(balls)::integer AS balls_faced,SUM(dismissals)::integer AS dismissals,
COUNT(*) FILTER(WHERE runs>=50 AND runs<100)::integer AS fifties,COUNT(*) FILTER(WHERE runs>=100)::integer AS hundreds,COUNT(*) FILTER(WHERE runs=0 AND dismissals>0)::integer AS ducks,
MAX(runs) AS highest_score,MAX(runs) FILTER(WHERE dismissals=0) AS highest_not_out_score
FROM actor_rows WHERE role=1 AND standard_stats GROUP BY player_key,scope_key,year;
CREATE UNIQUE INDEX player_batting_key ON player_batting(player_key,scope_key,year) NULLS NOT DISTINCT;
ANALYZE player_batting;
CREATE MATERIALIZED VIEW player_bowling AS
WITH totals AS(SELECT player_key,scope_key,year,COUNT(*)::integer AS observed_innings,SUM(runs)::integer AS runs_conceded,SUM(balls)::integer AS legal_balls,SUM(dismissals)::integer AS wickets,COUNT(*) FILTER(WHERE dismissals>=5)::integer AS five_wicket_innings FROM actor_rows WHERE role=2 AND standard_stats GROUP BY player_key,scope_key,year),
matches AS(SELECT player_key,scope_key,year,COUNT(*) FILTER(WHERE wickets>=10)::integer AS ten_wicket_matches FROM(SELECT player_key,scope_key,year,match_key,SUM(dismissals) AS wickets FROM actor_rows WHERE role=2 AND standard_stats GROUP BY player_key,scope_key,year,match_key) x GROUP BY player_key,scope_key,year),
best AS(SELECT DISTINCT ON(player_key,scope_key,year) player_key,scope_key,year,dismissals AS best_wickets,runs AS best_runs,balls AS best_legal_balls,match_key AS best_match_key,innings_number AS best_innings_number FROM actor_rows WHERE role=2 AND standard_stats ORDER BY player_key,scope_key,year,dismissals DESC,runs,date DESC NULLS LAST,match_id,innings_number)
SELECT t.*,m.ten_wicket_matches,b.best_wickets,b.best_runs,b.best_legal_balls,b.best_match_key,b.best_innings_number FROM totals t JOIN matches m ON(m.player_key=t.player_key AND m.scope_key=t.scope_key AND m.year IS NOT DISTINCT FROM t.year) JOIN best b ON(b.player_key=t.player_key AND b.scope_key=t.scope_key AND b.year IS NOT DISTINCT FROM t.year);
CREATE UNIQUE INDEX player_bowling_key ON player_bowling(player_key,scope_key,year) NULLS NOT DISTINCT;
ANALYZE player_bowling;
