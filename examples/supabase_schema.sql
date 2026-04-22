-- Supabase / Postgres schema for the esports content-strategy pipeline.
-- Paste the whole file into the SQL editor at
--   https://app.supabase.com/project/<your-project>/sql
-- and run once.

-- ---------------------------------------------------------------------------
-- Canonical entity tables
-- ---------------------------------------------------------------------------

create table if not exists teams (
  team_id     int  primary key,
  slug        text not null,
  name        text,
  added_at    timestamptz default now()
);

create table if not exists players (
  player_id   int  primary key,
  slug        text not null,
  name        text,
  team_id     int  references teams,
  added_at    timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- HLTV snapshots (one row per pull so you can track trends over time)
-- ---------------------------------------------------------------------------

create table if not exists hltv_team_overview (
  id                   bigserial primary key,
  team_id              int  not null references teams,
  window_start         date not null,
  window_end           date not null,
  maps_played          int,
  wins                 int,
  draws                int,
  losses               int,
  rounds_played        int,
  kd_ratio             numeric,
  win_rate_percent     numeric,
  fetched_at           timestamptz default now()
);
create index if not exists ix_team_overview_team_time on hltv_team_overview (team_id, fetched_at desc);

create table if not exists hltv_team_map_stats (
  id                        bigserial primary key,
  team_id                   int  not null references teams,
  window_start              date not null,
  window_end                date not null,
  map                       text not null,
  times_played              int,
  win_rate_percent          numeric,
  ct_round_win_percent      numeric,
  t_round_win_percent       numeric,
  fetched_at                timestamptz default now()
);
create index if not exists ix_team_map_team_time on hltv_team_map_stats (team_id, map, fetched_at desc);

create table if not exists hltv_rankings (
  snapshot_at  timestamptz not null,
  rank         int  not null,
  team_id      int  not null references teams,
  points       int,
  primary key (snapshot_at, rank)
);

create table if not exists hltv_player_snapshots (
  id                   bigserial primary key,
  player_id            int  not null references players,
  window_start         date not null,
  window_end           date not null,
  rating_2_0           numeric,
  kd_ratio             numeric,
  headshots_percent    numeric,
  kills_per_round      numeric,
  deaths_per_round     numeric,
  damage_per_round     numeric,
  maps_played          int,
  fetched_at           timestamptz default now()
);

create table if not exists hltv_results (
  match_id     bigint primary key,
  team1        text,
  team2        text,
  score        text,
  event        text,
  played_at    text,
  match_url    text,
  inserted_at  timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- Steam Market daily prices + 24h volume
-- ---------------------------------------------------------------------------

create table if not exists watched_items (
  market_hash_name text primary key,
  appid            int  default 730,
  category         text,                   -- 'sticker' / 'skin' / 'capsule' — your call
  notes            text,
  added_at         timestamptz default now()
);

create table if not exists steam_prices (
  id                bigserial primary key,
  market_hash_name  text not null references watched_items,
  lowest_price      numeric,
  median_price      numeric,
  volume_24h        int,
  currency          int  default 1,
  fetched_at        timestamptz not null,
  inserted_at       timestamptz default now()
);
create index if not exists ix_steam_prices_item_time on steam_prices (market_hash_name, fetched_at desc);

-- ---------------------------------------------------------------------------
-- EsportsCharts viewership snapshots
-- ---------------------------------------------------------------------------

create table if not exists tournament_viewership (
  slug              text not null,
  game              text not null,
  tournament        text,
  peak_viewers      bigint,
  avg_viewers       bigint,
  hours_watched     bigint,
  airtime_hours     numeric,
  fetched_at        timestamptz default now(),
  primary key (slug, fetched_at)
);

-- ---------------------------------------------------------------------------
-- Content briefings (the output of your Claude node)
-- ---------------------------------------------------------------------------

create table if not exists briefings (
  id           bigserial primary key,
  briefed_at   timestamptz default now(),
  topic        text,                 -- e.g. 'daily' / 'BIG roster shift'
  angle        text,                 -- short headline
  body_md      text,                 -- full markdown post draft
  source_data  jsonb,                -- the JSON you fed to Claude
  posted_to    text[]                -- ['telegram','twitter']
);

-- Audit log of Telegram bot interactions — useful for debugging
-- and for seeing which questions you ask most often.
create table if not exists telegram_queries (
  id           bigserial primary key,
  asked_at     timestamptz default now(),
  chat_id      bigint,
  user_name    text,
  command      text,
  args         text,
  response_ok  boolean
);
