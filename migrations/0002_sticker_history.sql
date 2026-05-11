-- ============================================================================
--  0002_sticker_history — UP
--
--  Phase-2 of the CS2 sticker analytics: historical price series + a
--  catalog mapping market_hash_name → (event, category, team, player,
--  placement_tier). Lets us write the ROI / "best Major sticker
--  investment" posts that compare e.g. ELEAGUE Atlanta 2017 vs
--  PGL Stockholm 2021.
--
--  Design notes:
--
--  * `sticker_price_history` is the multi-source time series. Each row
--    is one (item, date, source) sample. `source` ∈ {'pricempire',
--    'steam_market'} so we can blend Pricempire's clean aggregates with
--    a Steam-Market fallback that fills pre-Pricempire dates (Atlanta
--    2017 is older than Pricempire itself, founded 2018).
--    Unique key (market_hash_name, date, source) keeps backfills
--    idempotent — re-running a 180-day window does not duplicate rows.
--
--  * Date granularity is intentionally `date` (not `timestamptz`).
--    Pricempire's history endpoint is daily, and we don't need finer
--    grain for ROI math. The intraday `steam_prices` snapshots from
--    cron still live in their own table, untouched.
--
--  * `sticker_catalog` is the per-sticker classification used by the
--    viz layer (`category` drives line color, `placement_tier` drives
--    top3-vs-rest line style). Seeded by `seeds/sticker_catalog.py`
--    for each Major.
--      - `category` is the upstream-correct name, not a unified label:
--        Atlanta 2017 has 'foil', Stockholm 2021 has 'glitter'. Posts
--        get the real name; never alias them into 'premium'.
--      - `placement_tier` ∈ {'top3', 'rest', 'champion', 'finalist'}.
--        Champion / finalist apply to autograph categories where each
--        sticker belongs to one player on a specific team.
--
--  Apply:    psql "$SUPABASE_URL" -f migrations/0002_sticker_history.sql
--  Rollback: migrations/0002_sticker_history_down.sql
-- ============================================================================

begin;

-- -----------------------------------------------------------------------------
-- sticker_catalog — master list, one row per market_hash_name
-- -----------------------------------------------------------------------------
create table if not exists sticker_catalog (
    market_hash_name text primary key,
    event_slug       text not null,                -- e.g. 'eleague-atlanta-2017'
    category         text not null,                -- 'paper' | 'holo' | 'foil' | 'glitter' | 'gold' | 'champion_gold'
    team_name        text,
    player_name      text,                         -- only set for autograph SKUs
    placement_tier   text not null default 'rest', -- 'top3' | 'rest' | 'champion' | 'finalist'
    added_at         timestamptz not null default now()
);

create index if not exists ix_sticker_catalog_event
    on sticker_catalog (event_slug);

create index if not exists ix_sticker_catalog_event_cat
    on sticker_catalog (event_slug, category);

create index if not exists ix_sticker_catalog_event_cat_tier
    on sticker_catalog (event_slug, category, placement_tier);

-- -----------------------------------------------------------------------------
-- sticker_price_history — daily price series, multi-source
-- -----------------------------------------------------------------------------
create table if not exists sticker_price_history (
    id                bigserial primary key,
    market_hash_name  text not null,
    date              date not null,
    source            text not null,        -- 'pricempire' | 'steam_market'
    lowest_price_usd  numeric(12, 4),
    median_price_usd  numeric(12, 4),
    volume            integer,
    raw_payload       jsonb,
    fetched_at        timestamptz not null default now(),
    constraint uq_sticker_price_history__name_date_source
        unique (market_hash_name, date, source)
);

create index if not exists ix_sticker_price_history_name_date
    on sticker_price_history (market_hash_name, date desc);

create index if not exists ix_sticker_price_history_date
    on sticker_price_history (date);

-- -----------------------------------------------------------------------------
-- sticker_price_history_blended — convenience view picking one row per
-- (item, date) preferring 'pricempire' over 'steam_market'. Phase-2 viz
-- queries from this view so callers don't have to repeat the dedup logic.
-- -----------------------------------------------------------------------------
create or replace view sticker_price_history_blended as
select distinct on (market_hash_name, date)
       market_hash_name,
       date,
       source,
       lowest_price_usd,
       median_price_usd,
       volume
  from sticker_price_history
 order by market_hash_name, date,
          case source when 'pricempire' then 0 when 'steam_market' then 1 else 2 end;

commit;
