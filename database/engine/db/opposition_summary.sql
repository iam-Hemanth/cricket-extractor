CREATE SCHEMA cricket_opposition_read_v1;
SET search_path=cricket_opposition_read_v1,pg_catalog;
CREATE TABLE read_environment(singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),ddl_sha256 TEXT NOT NULL,source_run_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,codebook_sha256 TEXT NOT NULL,cluster_run_id TEXT NOT NULL);
CREATE TABLE teams(key SMALLINT PRIMARY KEY CHECK(key>0),name TEXT UNIQUE NOT NULL);
INSERT INTO teams SELECT ROW_NUMBER() OVER(ORDER BY team_name)::smallint,team_name FROM cricket_compact_v1.match_teams GROUP BY team_name;
ANALYZE teams;
CREATE FUNCTION immutable_team() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RAISE EXCEPTION 'raw team identities are immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER teams_immutable BEFORE UPDATE OR DELETE ON teams FOR EACH ROW EXECUTE FUNCTION immutable_team();
CREATE TRIGGER teams_no_truncate BEFORE TRUNCATE ON teams FOR EACH STATEMENT EXECUTE FUNCTION immutable_team();
CREATE VIEW actor_opposition AS SELECT v.*,t.key AS opposition_key,t.name AS opposition FROM cricket_player_read_v1.actor_rows v
JOIN cricket_compact_v1.innings i USING(match_key,innings_number)
JOIN teams t ON t.name=CASE WHEN v.role=1 THEN i.bowling_team ELSE i.batting_team END;
CREATE MATERIALIZED VIEW player_vs_team AS SELECT role,player_key,opposition_key,COUNT(DISTINCT match_key)::smallint AS observed_matches,COUNT(*)::smallint AS observed_innings,
SUM(runs)::smallint AS runs,SUM(balls)::smallint AS balls,SUM(dismissals)::smallint AS dismissals FROM actor_opposition WHERE standard_stats GROUP BY role,player_key,opposition_key;
CREATE UNIQUE INDEX player_vs_team_key ON player_vs_team(player_key,role,opposition_key) WITH(fillfactor=100);
ANALYZE player_vs_team;
