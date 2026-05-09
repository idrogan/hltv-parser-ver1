-- ============================================================================
--  0001_pandascore_liquipedia_stickers — UP
--
--  Adds the schema needed for Phase 1 of the CS2 content pipeline:
--    • Liquipedia + PandaScore tournaments / matches / metadata
--    • A scraper-run audit log
--    • Steam Market sticker tracking (watched_items + steam_prices)
--
--  Design notes:
--
--  * `source` ∈ {'liquipedia', 'pandascore', 'hltv', 'manual'}. Same
--    logical entity from two sources is intentionally stored as two
--    rows. Dedup / canonicalization is deferred to Phase 2.
--    `unique (source, source_id)` is the upsert key everywhere.
--
--  * Foreign keys (`matches.tournament_id`, `tournament_prize_distribution
--    .tournament_id`, `players_meta.team_id`) reference the local
--    bigserial id, NOT (source, source_id), because matches scraped from
--    PandaScore can be linked to a Liquipedia-sourced tournament only
--    after Phase-2 dedup. They are nullable for now: insert without a
--    parent is allowed, dedup will backfill.
--
--  * `raw_payload jsonb` on matches captures the upstream API response
--    so Phase 2 can re-derive fields without re-fetching.
--
--  * Every parser run writes one row to `_scraper_runs`. This is the
--    single source of truth for "is the pipeline alive". `meta jsonb`
--    holds free-form things like PandaScore quota remaining.
--
--  * Stickers: `watched_items` is a curated seed list of Steam Market
--    `market_hash_name`s, optionally grouped by `event_slug` so a
--    major-stickers post can join cleanly. `steam_prices` is append-only
--    so trends are queryable by `(market_hash_name, fetched_at desc)`.
--
--  Apply:    psql "$SUPABASE_URL" -f migrations/0001_pandascore_liquipedia_stickers.sql
--    or paste into the Supabase SQL editor.
--  Rollback: migrations/0001_pandascore_liquipedia_stickers_down.sql
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1. Tournaments
-- ---------------------------------------------------------------------------
create table if not exists tournaments (
    id              bigserial primary key,
    source          text        not null
                                check (source in ('liquipedia', 'pandascore', 'hltv', 'manual')),
    source_id       text        not null,           -- Liquipedia page name OR PandaScore numeric id (as text)
    name            text        not null,
    tier            text,                            -- 'S' / 'A' / 'B' / 'C' (Liquipedia) or PandaScore tier
    start_date      date,
    end_date        date,
    prize_pool_usd  numeric(14, 2),
    location        text,
    status          text,                            -- 'upcoming' / 'ongoing' / 'finished' / 'cancelled'
    raw_payload     jsonb,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now(),
    constraint tournaments_source_unique unique (source, source_id)
);

create index if not exists ix_tournaments_status_start
    on tournaments (status, start_date desc);
create index if not exists ix_tournaments_source_last_seen
    on tournaments (source, last_seen_at desc);

-- ---------------------------------------------------------------------------
-- 2. Matches
-- ---------------------------------------------------------------------------
create table if not exists matches (
    id              bigserial primary key,
    source          text        not null
                                check (source in ('liquipedia', 'pandascore', 'hltv', 'manual')),
    source_id       text        not null,
    tournament_id   bigint      references tournaments (id) on delete set null,
    team_a          text,
    team_b          text,
    score_a         int,
    score_b         int,
    scheduled_at    timestamptz,
    status          text,                            -- 'not_started' / 'live' / 'finished' / 'cancelled'
    raw_payload     jsonb,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now(),
    constraint matches_source_unique unique (source, source_id)
);

create index if not exists ix_matches_tournament      on matches (tournament_id);
create index if not exists ix_matches_scheduled       on matches (scheduled_at);
create index if not exists ix_matches_source_status   on matches (source, status);

-- ---------------------------------------------------------------------------
-- 3. Teams metadata
-- ---------------------------------------------------------------------------
create table if not exists teams_meta (
    id              bigserial primary key,
    source          text        not null
                                check (source in ('liquipedia', 'pandascore', 'hltv', 'manual')),
    source_id       text        not null,
    name            text        not null,
    region          text,
    raw_payload     jsonb,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now(),
    constraint teams_meta_source_unique unique (source, source_id)
);

create index if not exists ix_teams_meta_name on teams_meta (lower(name));

-- ---------------------------------------------------------------------------
-- 4. Players metadata
-- ---------------------------------------------------------------------------
create table if not exists players_meta (
    id              bigserial primary key,
    source          text        not null
                                check (source in ('liquipedia', 'pandascore', 'hltv', 'manual')),
    source_id       text        not null,
    nickname        text        not null,
    real_name       text,
    team_id         bigint      references teams_meta (id) on delete set null,
    raw_payload     jsonb,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now(),
    constraint players_meta_source_unique unique (source, source_id)
);

create index if not exists ix_players_meta_nickname on players_meta (lower(nickname));
create index if not exists ix_players_meta_team     on players_meta (team_id);

-- ---------------------------------------------------------------------------
-- 5. Tournament prize distribution (Liquipedia only for now)
-- ---------------------------------------------------------------------------
create table if not exists tournament_prize_distribution (
    id              bigserial primary key,
    tournament_id   bigint      not null references tournaments (id) on delete cascade,
    place           text        not null,            -- '1st' / '2nd' / '3rd-4th' / 'Group Stage' etc.
    team_or_player  text        not null,
    amount_usd      numeric(14, 2),
    points          numeric,                         -- Liquipedia sometimes lists ranking points instead of $
    notes           text,
    fetched_at      timestamptz not null default now()
);

create index if not exists ix_prize_dist_tournament on tournament_prize_distribution (tournament_id);

-- ---------------------------------------------------------------------------
-- 6. Scraper run audit log — single source of truth for pipeline liveness
-- ---------------------------------------------------------------------------
create table if not exists _scraper_runs (
    id              bigserial primary key,
    scraper_name    text        not null,            -- 'liquipedia' / 'pandascore' / 'steam_prices' / 'hltv'
    started_at      timestamptz not null default now(),
    finished_at     timestamptz,
    status          text        not null
                                check (status in ('running', 'ok', 'error', 'partial')),
    rows_written    int         default 0,
    error           text,                            -- exception message / structured failure detail
    meta            jsonb                            -- e.g. {"quota_remaining": 8200, "endpoint": "..."}
);

create index if not exists ix_scraper_runs_name_time
    on _scraper_runs (scraper_name, started_at desc);

-- ---------------------------------------------------------------------------
-- 7. Steam Market sticker tracking
--
-- Defensive: an earlier aspirational schema (examples/supabase_schema.sql)
-- may already have created `watched_items` and `steam_prices` without
-- `event_slug`. `create table if not exists` is a no-op against a
-- pre-existing table — it does NOT add new columns. So we follow up
-- with `alter table ... add column if not exists` for any column that
-- wasn't in the legacy shape. Likewise, `steam_prices` keeps the
-- legacy column names (`lowest_price`, `median_price`) so the table
-- definition matches in both fresh and pre-existing databases.
-- ---------------------------------------------------------------------------
create table if not exists watched_items (
    market_hash_name text        primary key,
    appid            int         not null default 730,
    category         text,
    event_slug       text,
    notes            text,
    added_at         timestamptz not null default now()
);

alter table watched_items add column if not exists event_slug text;

create index if not exists ix_watched_items_event on watched_items (event_slug);

create table if not exists steam_prices (
    id                bigserial primary key,
    market_hash_name  text        not null references watched_items (market_hash_name) on delete cascade,
    lowest_price      numeric(12, 2),
    median_price      numeric(12, 2),
    volume_24h        int,
    currency          int         not null default 1,
    fetched_at        timestamptz not null,
    inserted_at       timestamptz not null default now()
);

create index if not exists ix_steam_prices_item_time
    on steam_prices (market_hash_name, fetched_at desc);

commit;
