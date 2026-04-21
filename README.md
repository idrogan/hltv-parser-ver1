# esports-data-api

Self-hosted JSON API for an esports content-strategy pipeline. Scrapes
**HLTV.org**, **Steam Community Market** and **EsportsCharts.com**,
returns clean JSON, and is designed to sit at **Layer 1** of a
Make / n8n → Supabase → Claude → Telegram automation.

## What you can pull

### HLTV  — `/hltv/*`
| Endpoint | Purpose |
|---|---|
| `GET /hltv/team/{id}/{slug}/overview` | KD, win rate, rounds over time window |
| `GET /hltv/team/{id}/{slug}/maps`     | **Per-map CT/T side round winrate** |
| `GET /hltv/team/{id}/{slug}/matches`  | Match history in the window |
| `GET /hltv/team/search?name=...`      | Resolve team name → id + slug |
| `GET /hltv/player/{id}/{slug}`        | Rating 2.0, KD, HS%, ADR, KPR/DPR |
| `GET /hltv/rankings`                  | World ranking top 30 |
| `GET /hltv/matches/upcoming`          | Upcoming matches |
| `GET /hltv/results?offset=0`          | Recent results (paginate) |

All time-windowed endpoints accept: `start_date=YYYY-MM-DD` & `end_date=YYYY-MM-DD`, or `months_back=5`.

### Steam Community Market  — `/steam/*`
| Endpoint | Purpose |
|---|---|
| `GET /steam/price?market_hash_name=...` | Current lowest/median price + **24-hour sold volume** |
| `POST /steam/price/bulk`                 | Same for a list of names in one call |
| `GET /steam/search?query=...`           | Browse items + listing counts |
| `GET /steam/history?market_hash_name=...` | Full lifetime per-sale history (requires `STEAM_LOGIN_SECURE` cookie) |

**Yes — units sold is available.** The 24-hour volume comes back on every
`/steam/price` call without auth. For multi-year historical volume, log
`/steam/price` daily to Postgres and you build history yourself, or
drop a `steamLoginSecure` cookie into `.env` to unlock the full
`/steam/history` endpoint in one shot.

`appid` defaults to `730` (CS2). Use `currency=1` (USD), `3` (EUR),
`5` (RUB), etc.

### EsportsCharts  — `/escharts/*`
| Endpoint | Purpose |
|---|---|
| `GET /escharts/tournaments?game=cs2&year=2025` | Tournament leaderboard — peak/avg viewers, hours watched, airtime |
| `GET /escharts/tournament/{game}/{slug}`       | Single-tournament detail incl. per-channel breakdown |

Games observed in the wild: `cs2`, `csgo`, `dota2`, `lol`, `valorant`,
`pubg`. EsportsCharts also sells a paid API — worth it if you scale
past personal use.

## Run it

### Docker (recommended)

```bash
cp .env.example .env       # set API_TOKEN + optional STEAM_LOGIN_SECURE
docker compose up -d --build
curl http://localhost:8000/health
```

### Local Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

### CLI (for cron + n8n "Execute Command")

```bash
python cli.py hltv team-maps 4608 natus-vincere --months-back 5
python cli.py hltv team 7532 big --start 2023-07-01 --end 2023-09-30

python cli.py steam price "Sticker | Titan (Holo) | Katowice 2014"
python cli.py steam search "Katowice 2014 Holo" --count 30
python cli.py steam history "AK-47 | Redline (Field-Tested)"

python cli.py escharts tournaments --game cs2 --year 2025
python cli.py escharts tournament cs2 iem-katowice-2024
```

Every command prints a single JSON document to stdout.

## Where this sits in your pipeline

```
┌──────────────────────── Layer 1: this API ─────────────────────────┐
│  /hltv/*       /steam/*        /escharts/*                         │
└─────────────────────────────┬──────────────────────────────────────┘
                              │  (HTTP Request node, 09:00 daily)
                              ▼
                  ┌──────────────────────┐
                  │  Layer 2: n8n        │
                  └──────────┬───────────┘
                             │
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
     Supabase (L3)    Claude API (L4)    Deduper / logic
                             │
                             ▼
                 ┌─────────────────────┐
                 │  Layer 5: Telegram  │
                 │  Layer 6: X autopost│
                 └─────────────────────┘
```

## n8n & Make wiring

Both apps use plain HTTP Request nodes:

- **URL** — `http://esports-data:8000/hltv/team/4608/natus-vincere/maps`
  (use the docker-compose service name, or `host.docker.internal` from
  desktop n8n)
- **Query** — `months_back=5` for HLTV; `market_hash_name=...` for Steam
- **Header** — `Authorization: Bearer {{$env.ESPORTS_API_TOKEN}}`

Reference inside downstream nodes:

- HLTV CT winrate:  `$json.overall_ct_round_win_percent`
- Steam 24h volume: `$json.volume_24h`
- EsC peak viewers: `$json.tournaments[0].peak_viewers`

Starter workflows are in [`examples/`](examples/):
- [`n8n_workflow.json`](examples/n8n_workflow.json) — HLTV CT-winrate digest
- [`make_blueprint.md`](examples/make_blueprint.md) — Make.com walkthrough
- [`steam_tracker.md`](examples/steam_tracker.md) — daily sticker price/volume → Supabase
- [`escharts_digest.md`](examples/escharts_digest.md) — weekly viewership leaderboard

## Auth

Set `API_TOKEN` in `.env` to require `Authorization: Bearer <token>`
on every request. Leave it empty only on a trusted private network.

## Rate-limit defaults

| Source         | Default throttle | Why |
|----------------|-----------------|-----|
| HLTV           | 2.0 s / request | Cloudflare + WAF, sub-1.5s gets banned |
| Steam          | 3.5 s / request | Valve 429s past ~20 req/min |
| EsportsCharts  | 2.0 s / request | Cloudflare, polite defaults |

Override per source via `HLTV_MIN_DELAY`, `STEAM_MIN_DELAY`,
`ESCHARTS_MIN_DELAY`. On heavy cron load, plug a residential proxy
into the matching `*_PROXY` env var.

## Project layout

```
app.py                    # FastAPI entry — mounts all three routers
cli.py                    # Multi-source CLI
common/                   # Shared bearer-auth dependency
hltv_parser/              # HLTV client + parsers + /hltv router
steam_market/             # Steam Market client + /steam router
escharts_parser/          # EsportsCharts client + parsers + /escharts router
Dockerfile
docker-compose.yml
examples/                 # n8n workflow JSON + Make/Steam/EsC guides
```

## Notes & limitations

- **HLTV and EsportsCharts** ship DOM changes occasionally. Parsers
  degrade to `null` fields rather than crashing — if a metric goes
  dark, open the matching `parsers.py` and update the CSS selector.
- **Steam Market** endpoints are public but undocumented. `priceoverview`
  and `search/render` have been stable for years; `pricehistory`
  requires a `steamLoginSecure` cookie and can rotate without warning.
- Scraping may violate HLTV/EsportsCharts ToS for commercial use.
  Throttle responsibly, buy the EsC paid API when you go pro, and
  consider HLTV's partner program.
