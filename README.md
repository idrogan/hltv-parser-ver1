# esports-data-api

Self-hosted JSON API for an esports content-strategy pipeline. Scrapes
**HLTV.org**, **Steam Community Market** and **EsportsCharts.com**,
returns clean JSON, and is designed to sit at **Layer 1** of an
n8n Cloud → Supabase → Claude → Telegram automation.

Primary deployment target is **Render.com** for the parser API +
**n8n Cloud** for orchestration. Local dev still works through
docker-compose.

> **Status (May 2026)**
> * HLTV scraper paused via `HLTV_ENABLED=false` — see
>   [`docs/RESUMING_HLTV.md`](docs/RESUMING_HLTV.md)
> * PandaScore (CS2) and Steam Market write directly to Supabase
> * Liquipedia parser scaffolded, gated on a `LIQUIPEDIA_API_KEY`
> * Architecture review: [`ARCHITECTURE_REVISION_2026_05.md`](ARCHITECTURE_REVISION_2026_05.md)

## Pipeline scrapers (write to Supabase)

```bash
# PandaScore — tournaments, matches, teams, players (CS2)
python cli.py pandascore run [--days 7 --matches-pages 6 --tournaments-pages 4]

# Liquipedia — tournaments + prize distribution (needs LIQUIPEDIA_API_KEY)
python cli.py liquipedia run [--tier-max 2 --months-back 12]

# Steam Market — sticker prices into steam_prices
echo 'Sticker | Natus Vincere (Holo) | Copenhagen 2024' \
  | python cli.py steam seed --event-slug pgl-copenhagen-2024
python cli.py steam refresh [--event-slug pgl-copenhagen-2024]
```

Schema lives under [`migrations/`](migrations/). Apply by pasting the
SQL into Supabase → SQL Editor or piping through `psql`.

## Cron on the DO droplet

```bash
# Edit crontab on the droplet:
crontab -e

# Paste from scripts/crontab.example — runs pandascore every 6h and
# steam refresh nightly at 02:00 UTC. Logs land in logs/<scraper>.log.
```

Wrapper script: [`scripts/run_scraper.sh`](scripts/run_scraper.sh).
Loads `.env`, activates `.venv`/`venv` if present, and timestamps every
log line.

## Visualization (Twitter/Substack-ready PNGs)

Three chart templates render directly from Supabase — each outputs
both a `1200x675` widescreen and a `1080x1080` square into `out/`:

```bash
# Top-N tournaments by prize pool (gold accent on the leader)
python cli.py viz prize-pool-ladder --year 2026 --limit 12 --from-source pandascore

# Single-tournament hero card with status pill, dates, prize, teams
python cli.py viz tournament-card --id 3

# Sticker prices: Holo vs Regular per team, headline price labels
python cli.py viz sticker-prices --event-slug pgl-copenhagen-2024
```

Sample renders are committed under [`out/samples/`](out/samples/).
Brand palette / typography / margins live in
[`viz/brand.py`](viz/brand.py) — change once, applies everywhere.

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

`appid` defaults to `730` (CS2). Use `currency=1` (USD), `3` (EUR), `5` (RUB), etc.

### EsportsCharts  — `/escharts/*`
| Endpoint | Purpose |
|---|---|
| `GET /escharts/tournaments?game=cs2&year=2025` | Tournament leaderboard — peak/avg viewers, hours watched, airtime |
| `GET /escharts/tournament/{game}/{slug}`       | Single-tournament detail incl. per-channel breakdown |

### Meta endpoints (always public, no auth)

| Endpoint | Purpose |
|---|---|
| `GET /health`  | Liveness probe used by Render's health check |
| `GET /warmup`  | Cheap wake-up call for cold containers — see *Cold-start handling* below |

---

## Deployment

### Option A — Render (primary)

1. Fork or push this repo to your own GitHub.
2. Render dashboard → **New → Blueprint** → connect the repo. Render
   reads `render.yaml` automatically and provisions a Docker web service.
3. Render generates a strong `API_TOKEN` for you on first deploy
   (`generateValue: true` in the blueprint). Copy it from the service's
   **Environment** tab — you will paste it into n8n.
4. After the first build, note the public URL — typically
   `https://esports-data-xxxx.onrender.com`.
5. Smoke-test:
   ```bash
   curl https://esports-data-xxxx.onrender.com/health
   # → {"ok":true,"sources":["hltv","steam","escharts"]}

   curl -H "Authorization: Bearer <API_TOKEN>" \
        https://esports-data-xxxx.onrender.com/hltv/rankings
   ```

The full step-by-step is in [`DEPLOY.md`](DEPLOY.md).

#### Cold-start handling (Free tier only)

Render Free containers spin down after ~15 min idle and take 30–60s
to cold-boot the next request. The repo ships a `/warmup` endpoint
exactly for this: in any latency-sensitive workflow (morning digest,
interactive Telegram bot), call `/warmup` first, wait ~30–45s in an
n8n **Wait** node, then issue the real requests. Pattern is documented
in [`DEPLOY.md`](DEPLOY.md#cold-start-warmup-pattern). Upgrading to
**Starter** ($7/mo) removes the cool-down entirely.

### Option B — Local development

Parser-only, against `n8n Cloud` or any external orchestrator:

```bash
cp .env.example .env       # set API_TOKEN
docker compose -f docker-compose.local.yml up --build
curl http://localhost:8000/health
```

Plain Python:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

### Option C — Self-hosted parser + self-hosted n8n on a single VPS

The original `docker-compose.full.yml` still works for users who want
both services on one box behind their own reverse proxy. See git
history of this README for the original walkthrough; nothing in
the parser has changed shape.

---

## n8n Cloud integration

### Set environment variables in n8n Cloud

n8n Cloud → **Settings → Variables**:

| Variable | Value |
|---|---|
| `ESPORTS_API_BASE` | `https://esports-data-xxxx.onrender.com` (your Render URL) |
| `TELEGRAM_CHAT_ID` | Your numeric chat id (from `/getUpdates`) |
| `ANTHROPIC_API_KEY`| Your Anthropic key |

### Add credentials in n8n Cloud

n8n Cloud → **Credentials**:

1. **HTTP Header Auth** — name `parser-api`. Header name `Authorization`,
   header value `Bearer <your Render API_TOKEN>`. This is the credential
   every HTTP Request node attaches when calling the parser.
2. **Telegram** — name `bot`. Bot token from @BotFather.
3. *(Optional)* Supabase, OpenAI/Anthropic node credentials, etc.

### Calling the API from a workflow

Every HTTP Request node points at:

```
{{ $env.ESPORTS_API_BASE }}/hltv/rankings
{{ $env.ESPORTS_API_BASE }}/hltv/team/4608/natus-vincere/maps?months_back=5
{{ $env.ESPORTS_API_BASE }}/steam/price?market_hash_name=...
```

Authentication: pick **Generic Credential Type → HTTP Header Auth →
parser-api**.

A ready-to-import workflow is in
[`examples/n8n_telegram_bot.json`](examples/n8n_telegram_bot.json).
It implements `/rank`, `/team` and `/compare`. n8n Cloud → **Workflows
→ Import from File** → activate → message your bot.

Smaller pattern docs:
- [`examples/make_blueprint.md`](examples/make_blueprint.md) — Make.com walkthrough
- [`examples/steam_tracker.md`](examples/steam_tracker.md) — daily sticker price/volume → Supabase
- [`examples/escharts_digest.md`](examples/escharts_digest.md) — weekly viewership digest
- [`examples/supabase_schema.sql`](examples/supabase_schema.sql) — DDL for every table the workflows write to

---

## Security notes for public deployment

The parser is hardened for the open internet:

- **Bearer token** required on every `/hltv/*`, `/steam/*`, `/escharts/*`
  route. Constant-time comparison; missing-vs-wrong returns identical 401.
- **Fail-fast misconfig**: when `API_REQUIRE_TOKEN=true` (the default in
  `render.yaml`) the app refuses to boot with an empty `API_TOKEN`. The
  build crashes loudly rather than silently exposing every scraper.
- **Per-IP rate limit** via `slowapi` — default 60 req/min/IP, configurable
  via `RATE_LIMIT_PER_MINUTE`. Real client IP is read from
  `X-Forwarded-For` (set by Render's edge), not the proxy hop.
- **CORS** — defaults to `*` for flexibility but should be locked to your
  n8n Cloud origin (`https://your-tenant.app.n8n.cloud`) via
  `ALLOWED_ORIGIN`.
- **Access log** — every request logged with method, path, IP, status,
  duration. The `Authorization` header value is **never** logged.

---

## Running tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Covered: bearer auth (200 / 401 / startup fail-fast), rate-limit 429
on burst, X-Forwarded-For-aware bucketing. Tests use FastAPI's ASGI
transport so they hit no real network.

---

## Project layout

```
app.py                    # FastAPI entry — mounts all three routers
cli.py                    # Multi-source CLI (cron / "Execute Command" friendly)
common/                   # Shared bearer-auth + rate-limit/access-log middleware
hltv_parser/              # HLTV client + parsers + /hltv router
steam_market/             # Steam Market client + /steam router
escharts_parser/          # EsportsCharts client + parsers + /escharts router
tests/                    # pytest suite (auth, rate-limit, real-IP)
render.yaml               # Render Blueprint
Dockerfile                # Honors $PORT, falls back to 8000 locally
docker-compose.local.yml  # Parser only, for local dev
docker-compose.full.yml   # Parser + n8n on the same network (single-VPS)
DEPLOY.md                 # Top-to-bottom deploy checklist
examples/                 # n8n workflow JSON + Make/Steam/EsC pattern docs + Supabase DDL
```

## Notes & limitations

- **HLTV and EsportsCharts** ship DOM changes occasionally. Parsers
  degrade to `null` fields rather than crashing — if a metric goes
  dark, open the matching `parsers.py` and update the CSS selector.
- **Steam Market** endpoints are public but undocumented. `priceoverview`
  and `search/render` have been stable for years; `pricehistory` requires
  a `steamLoginSecure` cookie and can rotate.
- Scraping may violate HLTV / EsportsCharts ToS for commercial use.
  Throttle responsibly. EsC sells a paid API once you grow.
