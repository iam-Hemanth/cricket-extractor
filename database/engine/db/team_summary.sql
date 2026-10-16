CREATE SCHEMA cricket_team_read_v1;
SET search_path=cricket_team_read_v1,pg_catalog;
CREATE TABLE read_environment(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),ddl_sha256 TEXT NOT NULL,source_run_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,codebook_sha256 TEXT NOT NULL,cluster_run_id TEXT NOT NULL);
CREATE TABLE venues(key SMALLINT PRIMARY KEY CHECK(key>0),venue TEXT,city TEXT,country TEXT,UNIQUE NULLS NOT DISTINCT(venue,city,country));
INSERT INTO venues SELECT ROW_NUMBER() OVER(ORDER BY venue NULLS LAST,city NULLS LAST,venue_country NULLS LAST)::smallint,venue,city,venue_country FROM cricket_compact_v1.matches GROUP BY venue,city,venue_country;
CREATE TABLE seasons(key SMALLINT PRIMARY KEY CHECK(key>0),season TEXT UNIQUE NULLS NOT DISTINCT);
INSERT INTO seasons SELECT ROW_NUMBER() OVER(ORDER BY season NULLS LAST)::smallint,season FROM cricket_compact_v1.matches GROUP BY season;
ANALYZE venues;ANALYZE seasons;
CREATE FUNCTION immutable_dimension() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'raw read dimensions require a new coherent generation' USING ERRCODE='23514'; END $$;
CREATE TRIGGER venues_immutable BEFORE UPDATE OR DELETE ON venues FOR EACH ROW EXECUTE FUNCTION immutable_dimension();
CREATE TRIGGER venues_no_truncate BEFORE TRUNCATE ON venues FOR EACH STATEMENT EXECUTE FUNCTION immutable_dimension();
CREATE TRIGGER seasons_immutable BEFORE UPDATE OR DELETE ON seasons FOR EACH ROW EXECUTE FUNCTION immutable_dimension();
CREATE TRIGGER seasons_no_truncate BEFORE TRUNCATE ON seasons FOR EACH STATEMENT EXECUTE FUNCTION immutable_dimension();

CREATE VIEW match_results AS
WITH pair AS(SELECT mt.match_id,MIN(t.key) AS team_a,MAX(t.key) AS team_b FROM cricket_compact_v1.match_teams mt JOIN cricket_opposition_read_v1.teams t ON t.name=mt.team_name GROUP BY mt.match_id HAVING COUNT(*)=2),
raw AS(SELECT m.*,ms.match_key,ms.scope_key,ms.year,p.team_a,p.team_b,v.key AS venue_key,se.key AS season_key,
 a.name AS name_a,b.name AS name_b,m.outcome->>'winner' AS raw_winner,m.outcome->>'result' AS raw_result,m.outcome->>'eliminator' AS eliminator
 FROM cricket_compact_v1.matches m JOIN pair p USING(match_id) JOIN cricket_player_read_v1.match_scope ms USING(match_id)
 JOIN cricket_opposition_read_v1.teams a ON a.key=p.team_a JOIN cricket_opposition_read_v1.teams b ON b.key=p.team_b
 JOIN venues v ON v.venue IS NOT DISTINCT FROM m.venue AND v.city IS NOT DISTINCT FROM m.city AND v.country IS NOT DISTINCT FROM m.venue_country
 JOIN seasons se ON se.season IS NOT DISTINCT FROM m.season)
SELECT raw.*,(CASE
 WHEN raw_result IS NULL AND eliminator IS NULL AND raw_winner=name_a THEN 1
 WHEN raw_result IS NULL AND eliminator IS NULL AND raw_winner=name_b THEN 2
 WHEN raw_result='tie' AND eliminator=name_a AND (raw_winner IS NULL OR raw_winner=eliminator) THEN 7
 WHEN raw_result='tie' AND eliminator=name_b AND (raw_winner IS NULL OR raw_winner=eliminator) THEN 8
 WHEN raw_result='tie' AND eliminator IS NULL AND raw_winner IS NULL THEN 3
 WHEN raw_result='draw' AND eliminator IS NULL AND raw_winner IS NULL THEN 4
 WHEN raw_result='no result' AND eliminator IS NULL AND raw_winner IS NULL THEN 5
 ELSE 6 END)::smallint AS result_code FROM raw;

-- These counters are independently compared with decoded source facts before publication.
CREATE VIEW ordinary_innings AS SELECT i.match_id,i.match_key,i.innings_number,i.batting_team,i.bowling_team,
 (i.source_scorecard->>'runs')::integer AS team_runs,(i.source_scorecard->>'wickets')::integer AS team_wickets,i.actual_legal_balls,
 i.penalty_runs,i.source_only_timed_out FROM cricket_compact_v1.innings i WHERE i.standard_stats;

CREATE MATERIALIZED VIEW team_vs_team AS SELECT team_a,team_b,scope_key,
 COUNT(*)::smallint AS matches,COUNT(*) FILTER(WHERE result_code IN(1,7))::smallint AS a_wins,
 COUNT(*) FILTER(WHERE result_code IN(2,8))::smallint AS b_wins,COUNT(*) FILTER(WHERE result_code=3)::smallint AS ties,
 COUNT(*) FILTER(WHERE result_code=4)::smallint AS draws,COUNT(*) FILTER(WHERE result_code=5)::smallint AS no_results,
 COUNT(*) FILTER(WHERE result_code=6)::smallint AS unclassified,COUNT(*) FILTER(WHERE result_code IN(7,8))::smallint AS decided_ties
 FROM match_results GROUP BY team_a,team_b,scope_key;
CREATE UNIQUE INDEX team_vs_team_key ON team_vs_team(team_a,team_b,scope_key) NULLS NOT DISTINCT WITH(fillfactor=100);

CREATE MATERIALIZED VIEW team_vs_team_seasons AS SELECT team_a,team_b,scope_key,year::smallint,season_key,
 COUNT(*)::smallint AS matches,COUNT(*) FILTER(WHERE result_code IN(1,7))::smallint AS a_wins,
 COUNT(*) FILTER(WHERE result_code IN(2,8))::smallint AS b_wins,COUNT(*) FILTER(WHERE result_code=3)::smallint AS ties,
 COUNT(*) FILTER(WHERE result_code=4)::smallint AS draws,COUNT(*) FILTER(WHERE result_code=5)::smallint AS no_results,
 COUNT(*) FILTER(WHERE result_code=6)::smallint AS unclassified,COUNT(*) FILTER(WHERE result_code IN(7,8))::smallint AS decided_ties
 FROM match_results GROUP BY team_a,team_b,scope_key,year,season_key;
CREATE UNIQUE INDEX team_vs_team_seasons_key ON team_vs_team_seasons(team_a,team_b,scope_key,year,season_key) NULLS NOT DISTINCT WITH(fillfactor=100);

CREATE MATERIALIZED VIEW team_recent_matches AS SELECT team_a,team_b,scope_key,date,match_key,year::smallint,result_code FROM match_results;
CREATE UNIQUE INDEX team_recent_matches_key ON team_recent_matches(team_a,team_b,scope_key,date DESC NULLS LAST,match_key DESC) NULLS NOT DISTINCT WITH(fillfactor=100);

CREATE VIEW venue_rows AS SELECT m.venue_key,m.scope_key,m.year,m.match_key,m.result_code,
 COALESCE(i.standard_innings,0) AS standard_innings,i.first_score,i.second_score,i.highest_team_total,i.lowest_ten_wicket_total,
 (m.playing_format IN('one_day','t20','hundred_ball') AND m.result_code IN(1,2) AND m.outcome->>'method' IS NULL
  AND i.first_team IS NOT NULL AND i.second_team IS NOT NULL AND i.first_team<>i.second_team) AS chase_eligible,
 m.raw_winner=i.second_team AS chase_won
 FROM match_results m LEFT JOIN LATERAL(SELECT COUNT(*) AS standard_innings,
 MAX(team_runs) FILTER(WHERE innings_number=1) AS first_score,MAX(team_runs) FILTER(WHERE innings_number=2) AS second_score,
 MAX(batting_team) FILTER(WHERE innings_number=1) AS first_team,MAX(batting_team) FILTER(WHERE innings_number=2) AS second_team,
 MAX(team_runs) AS highest_team_total,MIN(team_runs) FILTER(WHERE team_wickets=10) AS lowest_ten_wicket_total
 FROM ordinary_innings WHERE match_key=m.match_key) i ON true;
CREATE MATERIALIZED VIEW venue_stats AS SELECT venue_key,scope_key,year::smallint,COUNT(*)::smallint AS matches,
 COUNT(*) FILTER(WHERE result_code IN(1,2,7,8))::smallint AS decisive_matches,COUNT(*) FILTER(WHERE result_code=3)::smallint AS ties,
 COUNT(*) FILTER(WHERE result_code=4)::smallint AS draws,COUNT(*) FILTER(WHERE result_code=5)::smallint AS no_results,
 COUNT(*) FILTER(WHERE result_code=6)::smallint AS unclassified,SUM(standard_innings)::smallint AS standard_innings,
 COALESCE(SUM(first_score),0)::integer AS first_score_sum,COUNT(first_score)::smallint AS first_score_count,
 COALESCE(SUM(second_score),0)::integer AS second_score_sum,COUNT(second_score)::smallint AS second_score_count,
 MAX(highest_team_total)::smallint AS highest_team_total,MIN(lowest_ten_wicket_total)::smallint AS lowest_ten_wicket_total,
 COUNT(*) FILTER(WHERE chase_eligible)::smallint AS chase_decided_matches,COUNT(*) FILTER(WHERE chase_eligible AND chase_won)::smallint AS chase_wins
 FROM venue_rows GROUP BY venue_key,scope_key,year;
CREATE UNIQUE INDEX venue_stats_key ON venue_stats(venue_key,scope_key,year) NULLS NOT DISTINCT WITH(fillfactor=100);

-- Card candidates stay in ordinary views so explicit filters use the same definitions.
CREATE VIEW six_candidates AS SELECT v.batter_key AS player_key,v.scope_key,v.year,SUM(v.six_run_balls)::bigint AS value
 FROM cricket_matchup_read_v1.batter_vs_bowler v JOIN cricket_player_read_v1.scopes s ON s.key=v.scope_key
 WHERE s.playing_format='t20' AND s.record_class IN('t20','t20i') GROUP BY v.batter_key,v.scope_key,v.year;
CREATE VIEW total_candidates AS SELECT i.*,m.scope_key,m.year,t.key AS team_key FROM ordinary_innings i
 JOIN match_results m USING(match_key,match_id) JOIN cricket_opposition_read_v1.teams t ON t.name=i.batting_team;
CREATE VIEW figure_candidates AS SELECT a.*,i.bowling_team,t.key AS team_key FROM cricket_player_read_v1.actor_rows a
 JOIN ordinary_innings i USING(match_key,innings_number,match_id) JOIN cricket_opposition_read_v1.teams t ON t.name=i.bowling_team WHERE a.role=2 AND a.standard_stats;
CREATE VIEW activity_candidates AS SELECT DISTINCT a.player_key,a.match_key,a.scope_key,a.year FROM cricket_player_read_v1.actor_rows a WHERE a.standard_stats;
CREATE MATERIALIZED VIEW stat_cards AS
 SELECT 'most_t20_sixes'::text AS stat_id,
 (SELECT jsonb_build_object('player_key',player_key,'value',value) FROM(SELECT player_key,SUM(value)::bigint AS value FROM six_candidates GROUP BY player_key HAVING SUM(value)>0 ORDER BY SUM(value) DESC,player_key LIMIT 1)x) AS payload
 UNION ALL SELECT 'highest_total',(SELECT jsonb_build_object('team_key',team_key,'value',team_runs,'match_key',match_key,'innings_number',innings_number,'scope_key',scope_key) FROM total_candidates ORDER BY team_runs DESC,match_key,innings_number LIMIT 1)
 UNION ALL SELECT 'best_figures',(SELECT jsonb_build_object('player_key',player_key,'team_key',team_key,'wickets',dismissals,'runs_conceded',runs,'match_key',match_key,'innings_number',innings_number,'scope_key',scope_key) FROM figure_candidates ORDER BY dismissals DESC,runs,date DESC NULLS LAST,match_id,innings_number,player_key LIMIT 1)
 UNION ALL SELECT 'most_matches',(SELECT jsonb_build_object('player_key',player_key,'value',COUNT(DISTINCT match_key)) FROM activity_candidates GROUP BY player_key ORDER BY COUNT(DISTINCT match_key) DESC,player_key LIMIT 1);
CREATE UNIQUE INDEX stat_cards_key ON stat_cards(stat_id) NULLS NOT DISTINCT WITH(fillfactor=100);
ANALYZE team_vs_team;ANALYZE team_vs_team_seasons;ANALYZE team_recent_matches;ANALYZE venue_stats;ANALYZE stat_cards;
