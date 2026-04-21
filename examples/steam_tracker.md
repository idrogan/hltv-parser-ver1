# Steam sticker tracker — daily price + volume to Supabase

Pattern for logging sticker prices and 24h sold volume every day, so
you build your own multi-year history table in Supabase without needing
a logged-in `steamLoginSecure` cookie.

## Flow

```
[Cron: 09:05 daily]
        │
        ▼
[Supabase > Read rows]          ← table: watched_items (market_hash_name)
        │
        ▼
[Loop over items]
        │
        ▼
[HTTP Request > GET /steam/price]
   URL:     http://esports-data:8000/steam/price
   Query:   market_hash_name = {{$json.market_hash_name}}
            appid            = 730
            currency         = 1
   Headers: Authorization = Bearer {{$env.ESPORTS_API_TOKEN}}
        │
        ▼
[Supabase > Insert row]
   table: steam_prices
   columns:
     market_hash_name = {{$json.market_hash_name}}
     lowest_price     = {{$json.lowest_price}}
     median_price     = {{$json.median_price}}
     volume_24h       = {{$json.volume_24h}}
     fetched_at       = {{$json.fetched_at}}
```

Or do all items in one HTTP call:

```
POST http://esports-data:8000/steam/price/bulk
Content-Type: application/json
Authorization: Bearer ...

{
  "names": [
    "Sticker | Titan (Holo) | Katowice 2014",
    "Sticker | iBUYPOWER (Holo) | Katowice 2014",
    "Sticker | Vox Eminor (Holo) | Katowice 2014"
  ],
  "currency": 1
}
```

Response — one row per item, `volume_24h` is the **number of units sold
in the last 24 hours**:

```json
{
  "items": [
    {
      "market_hash_name": "Sticker | Titan (Holo) | Katowice 2014",
      "lowest_price": 27500.0,
      "median_price": 29999.99,
      "volume_24h": 3,
      "fetched_at": "2026-04-21T09:05:12Z"
    },
    ...
  ]
}
```

## Suggested Supabase schema

```sql
create table watched_items (
  market_hash_name text primary key,
  appid            int default 730,
  added_at         timestamptz default now()
);

create table steam_prices (
  id                bigserial primary key,
  market_hash_name  text not null references watched_items,
  lowest_price      numeric,
  median_price      numeric,
  volume_24h        int,
  fetched_at        timestamptz not null,
  inserted_at       timestamptz default now()
);

create index on steam_prices (market_hash_name, fetched_at desc);
```

After a month you can write Claude prompts like *"stickers whose daily
volume has grown >200% week-over-week, sorted by absolute volume"* —
exactly the "which stickers are pumping" content angle.

## Full lifetime history (optional)

If you drop a `steamLoginSecure` cookie into `.env`, the one-shot
`/steam/history?market_hash_name=...` endpoint returns every sale
since the item was listed — no daily logging needed. The cookie
rotates occasionally and tying it to an account is against Steam's
ToS, so use at your own risk.
