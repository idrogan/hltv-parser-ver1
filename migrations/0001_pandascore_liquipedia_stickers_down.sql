-- ============================================================================
--  0001_pandascore_liquipedia_stickers — DOWN
--
--  Drops everything created by the matching UP migration. Drop order is
--  the reverse of create order so foreign keys don't block the drop.
--
--  WARNING: this destroys all rows in these tables. If you've started
--  writing real data, dump first:
--      pg_dump --data-only --table=tournaments --table=matches \
--              --table=teams_meta --table=players_meta \
--              --table=tournament_prize_distribution \
--              --table=_scraper_runs \
--              --table=watched_items --table=steam_prices \
--              "$SUPABASE_URL" > backup_0001.sql
-- ============================================================================

begin;

drop table if exists steam_prices                  cascade;
drop table if exists watched_items                 cascade;
drop table if exists _scraper_runs                 cascade;
drop table if exists tournament_prize_distribution cascade;
drop table if exists players_meta                  cascade;
drop table if exists teams_meta                    cascade;
drop table if exists matches                       cascade;
drop table if exists tournaments                   cascade;

commit;
