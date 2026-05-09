# RECON — 2026-05-09

Read-only audit of the repo as it stands today, before any Phase 1 work.
Branch: `claude/cs2-pipeline-integration-gmJPW`. Working tree clean, head
at `5a0854b`.

## 1. Stack facts (the unknowns from the brief)

| Question | Answer | Evidence |
|---|---|---|
| Python version | **3.12** | `Dockerfile`: `FROM python:3.12-slim` |
| Package manager | **pip + `requirements.txt`** | `requirements.txt`, `requirements-dev.txt`; no `pyproject.toml`, `uv.lock`, `poetry.lock`, or `Pipfile` |
| `CLAUDE.md` in repo? | **No** | not present at root |
| Tests? | **Yes, but only API-shell tests.** `tests/test_auth.py`, `tests/test_rate_limit.py`. No parser tests, no scraper tests, no Supabase tests. `pytest.ini` configured (`asyncio_mode = auto`). | `tests/`, `pytest.ini` |
| Where do secrets live? | **`.env`** (gitignored), with `.env.example` as template. `common/__init__.py` reads `API_TOKEN` / `API_REQUIRE_TOKEN` from env. No hardcoded credentials found in source. Scrapers also read `*_PROXY`, `*_MIN_DELAY`, `STEAM_LOGIN_SECURE` from env. | `.env.example`, `common/__init__.py`, `*/router.py` |

Runtime deps (pinned): `curl_cffi 0.7.4`, `selectolax 0.3.27`, `fastapi 0.115.6`,
`uvicorn[standard] 0.32.1`, `pydantic 2.10.3`, `python-dateutil 2.9.0.post0`,
`tenacity 9.0.0`, `python-dotenv 1.0.1`, `slowapi 0.1.9`. Dev: `pytest 8.3.4`,
`httpx 0.28.1`.

**Notably absent:** no Postgres / Supabase client (`psycopg`, `supabase`,
`sqlalchemy`, `postgrest`), no migrations tool (`alembic`, `yoyo`,
`sqitch`), no scheduler (`apscheduler`, `celery`). The Python side has
**never written a row to Supabase from its own process** — see §3.

## 2. Folder structure (`tree -L 2`)

```
.
├── .env.example
├── .gitignore
├── DEPLOY.md
├── Dockerfile
├── README.md
├── app.py                       # FastAPI entrypoint
├── cli.py                       # one-shot scraper CLI
├── common/
│   ├── __init__.py              # bearer auth helpers
│   └── middleware.py            # slowapi rate limiting
├── docker-compose.yml           # minimal single-service
├── docker-compose.local.yml     # parser only, ports exposed
├── docker-compose.full.yml      # parser + n8n on same network
├── escharts_parser/
│   ├── __init__.py
│   ├── client.py
│   ├── parsers.py
│   ├── router.py
│   └── service.py
├── examples/
│   ├── escharts_digest.md
│   ├── make_blueprint.md
│   ├── n8n_telegram_bot.json
│   ├── n8n_workflow.json
│   ├── steam_tracker.md
│   └── supabase_schema.sql      # NOT applied; aspirational
├── hltv_parser/
│   ├── __init__.py
│   ├── client.py
│   ├── parsers.py
│   ├── router.py
│   ├── service.py
│   └── util.py
├── pytest.ini
├── render.yaml
├── requirements.txt
├── requirements-dev.txt
├── steam_market/
│   ├── __init__.py
│   ├── client.py
│   ├── router.py
│   └── service.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_auth.py
    └── test_rate_limit.py
```

No `migrations/` directory. No `liquipedia*`, `pandascore*`, or
`supabase*` modules anywhere in `*.py`.

## 3. What runs end-to-end today

**One thing runs: a read-only JSON HTTP API.** Two entry points expose
it; both produce JSON to stdout / HTTP, neither writes to a database.

| Entry point | Command | What it produces | Where it writes |
|---|---|---|---|
| `app.py` (FastAPI) | `uvicorn app:app …` (Render / docker) | JSON over HTTP under `/hltv/*`, `/steam/*`, `/escharts/*`, plus `/health` and `/warmup` | HTTP response only |
| `cli.py` | `python cli.py <source> <cmd> …` | Single JSON document on stdout | stdout only |

**No Python code calls Supabase.** A repo-wide grep for
`supabase|psycopg|postgres|sqlalchemy|insert` returns only doc strings
(e.g. `cli.py:5` mentions "a Supabase row insert" as a *suggested*
downstream use). The intended architecture is:

```
   Python parsers (this repo)  →  HTTP JSON  →  n8n  →  Supabase
```

n8n is the only thing that was ever supposed to talk to Supabase. The
owner reports no rows have actually been written yet.

Bundled n8n example (`examples/n8n_workflow.json`) is the "weekly Monday
09:00 CT-side winrate digest" — it fetches `/hltv/team/{id}/{slug}/maps`
and stops. It does **not** include a Supabase insert step. So even the
example is currently a read-only proof-of-life.

## 4. HLTV touchpoints

Code:
- `hltv_parser/__init__.py`, `client.py` (curl_cffi + Cloudflare-aware
  retry), `parsers.py` (selectolax), `service.py`, `router.py`,
  `util.py`. ~820 LOC.
- `app.py` mounts `hltv_router`, registers HLTV exception handlers.
- `cli.py` has the `hltv` subcommand tree (`team`, `team-maps`,
  `team-matches`, `player`, `search-team`, `rankings`, `upcoming`,
  `results`).
- `Dockerfile` copies `hltv_parser` into the image.

Env vars:
- `HLTV_MIN_DELAY` (default `2.0`)
- `HLTV_PROXY` (optional)
- No `HLTV_ENABLED` flag exists — it has to be added in Phase 1.1.

n8n / external:
- `examples/n8n_workflow.json` (weekly HLTV digest, only HLTV)
- `examples/n8n_telegram_bot.json` references HLTV endpoints
- `render.yaml` declares `HLTV_MIN_DELAY` and `HLTV_PROXY`
- `docker-compose.full.yml` propagates `API_TOKEN` (used by both)

DB tables tied to HLTV (in `examples/supabase_schema.sql`, **not applied
to the live DB**): `hltv_team_overview`, `hltv_team_map_stats`,
`hltv_rankings`, `hltv_player_snapshots`, `hltv_results`. Plus shared
`teams` and `players` referenced by FK.

Cron jobs: **none in this repo.** Any scheduling lives in the n8n
instance on the droplet; not visible from here.

## 5. Existing Supabase schema

**Cannot verify directly** — no `SUPABASE_URL` / service-role key is
present in the local environment, and the repo carries no DB
introspection script. Per the brief, the owner reports the schema is
**empty**.

What the repo *aspires* to (in `examples/supabase_schema.sql`, written
but never applied):

- `teams (team_id pk, slug, name, added_at)`
- `players (player_id pk, slug, name, team_id fk, added_at)`
- `hltv_team_overview` — windowed KD/winrate snapshots
- `hltv_team_map_stats` — per-map CT/T winrate
- `hltv_rankings` — top-30 snapshots
- `hltv_player_snapshots`
- `hltv_results` — per-match
- `watched_items`, `steam_prices`
- `tournament_viewership` (escharts)
- `briefings` (Claude output), `telegram_queries`

Treat all of the above as a starting point for the new schema, not
as live tables. The Phase 1.2 migrations will be additive on top of
whatever (if anything) currently exists.

## 6. `.env` keys (names only)

From `.env.example` (the only canonical list — there is no live `.env`
in the repo):

- `API_TOKEN`
- `API_REQUIRE_TOKEN`
- `PORT`
- `LOG_LEVEL`
- `RATE_LIMIT_PER_MINUTE`
- `ALLOWED_ORIGIN`
- `HLTV_MIN_DELAY`
- `STEAM_MIN_DELAY`
- `ESCHARTS_MIN_DELAY`
- `HLTV_PROXY`
- `STEAM_PROXY`
- `ESCHARTS_PROXY`
- `STEAM_LOGIN_SECURE`

Referenced by `docker-compose.full.yml` but **not** in `.env.example`
(would need to be added):

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE`
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `N8N_HOST`, `N8N_PROTOCOL`, `WEBHOOK_URL`, `TIMEZONE`

Phase 1 will need to add at minimum: `HLTV_ENABLED`, `LIQUIPEDIA_CONTACT_EMAIL`,
`PANDASCORE_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`.

No hardcoded secrets were found anywhere in source.

## 7. Honest assessment — fastest path to data in Supabase

The unspoken root cause behind "schema is empty" is structural:
**the Python side has no Supabase writer.** It produces JSON and stops.
Every architectural diagram assumes n8n closes the loop, but no n8n
workflow that actually writes has been built yet. So the choice for
Phase 1 is:

**Option A — keep writes in n8n.** Add Liquipedia + PandaScore HTTP
endpoints, add Supabase HTTP/Insert nodes in n8n. Pros: matches the
existing pattern; nothing new in the Python repo. Cons: every parser
needs an n8n workflow built; quota/rate-limit headers from PandaScore
have to be plumbed through n8n; `_scraper_runs` logging is awkward.

**Option B — give Python a tiny Supabase writer (recommended).** Add a
`sb_writer.py` (PostgREST via `httpx`, no ORM) that the Liquipedia and
PandaScore packages call directly at the end of a run. n8n's only job
becomes scheduling: it triggers `python cli.py liquipedia run` /
`python cli.py pandascore run` via Execute Command, and the Python
process writes rows + a `_scraper_runs` audit row in the same
transaction-scoped batch. This keeps quota tracking, error handling,
and `_scraper_runs` logging in one language and matches the brief's
"first useful data fast, no fancy abstractions" framing.

I'd go with **B**. It's roughly the same amount of new code, but it
removes the "I have to remember to wire up n8n" step that has clearly
been the bottleneck so far. HLTV is paused so we don't backfill it
through n8n — it stays a JSON HTTP endpoint until budget allows.

Concretely, Phase 1 in this shape is:

1. `HLTV_ENABLED=false` flag at every HLTV entry point (~10 min).
2. Add `migrations/0001_pandascore_liquipedia.sql` with the new tables
   from §1.2 of the brief, applied additively (`create table if not
   exists`). Ask before running against the live DB.
3. New `liquipedia_parser/` and `pandascore_parser/` packages mirroring
   the shape of `hltv_parser/`. Each ships a `run()` callable and a
   `cli.py` subcommand.
4. New `common/sb_writer.py` — small PostgREST wrapper using `httpx`
   and `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE`.
5. New `_scraper_runs` write at the start and end of every run.
6. Two n8n schedule triggers (Execute Command), one per parser.

That's roughly a day of work and gets real rows into Supabase by end of
this week. Refactoring (deduplication, multi-source canonical entities,
HLTV unpause) lands in Phase 2 / 3 once data is flowing.
