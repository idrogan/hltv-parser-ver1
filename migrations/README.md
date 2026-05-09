# Migrations

Plain numbered SQL files. No tooling dependency — apply by piping
through `psql` or pasting into the Supabase SQL editor.

## Naming

```
NNNN_short_description.sql            # forward (UP)
NNNN_short_description_down.sql       # rollback (DOWN)
```

`NNNN` is a four-digit, zero-padded, monotonically-increasing integer.
Never reuse a number; if a migration is wrong, write `NNNN+1` to fix
it. The DOWN file must restore the schema to its pre-UP state.

## Applying to Supabase

### Option A — `psql`
```bash
export SUPABASE_DB_URL='postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres'
psql "$SUPABASE_DB_URL" -f migrations/0001_pandascore_liquipedia_stickers.sql
```

The connection string lives in **Supabase → Project Settings → Database
→ Connection string → URI** (use the *direct connection*, not the
pooler, for DDL).

### Option B — Supabase SQL editor

1. Open `https://app.supabase.com/project/<id>/sql/new`
2. Paste the file contents
3. Run

Both options are idempotent — every `create` uses `if not exists`, so
re-running a file is a no-op.

## Rolling back

```bash
psql "$SUPABASE_DB_URL" -f migrations/0001_pandascore_liquipedia_stickers_down.sql
```

DOWN files use `drop ... cascade`, so any dependent objects (or rows)
disappear with the table. Read the warning at the top of each DOWN
file before running.

## What's where

| File | Adds |
|---|---|
| `0001_pandascore_liquipedia_stickers.sql` | `tournaments`, `matches`, `teams_meta`, `players_meta`, `tournament_prize_distribution`, `_scraper_runs`, `watched_items`, `steam_prices` |
