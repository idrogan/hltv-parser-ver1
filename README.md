# hltv-parser

Python scraper for HLTV.org that exposes a small JSON API designed to be
consumed from **Make** (Integromat) or **n8n** for content-strategy
automations.

It supports time-windowed queries — e.g. *"CT-side round winrate for
NAVI, last 5 months"* — by passing `startDate`/`endDate` (or a
`months_back` shortcut) to HLTV's stat pages and returning structured
JSON.

## What you can pull

| Endpoint                                     | What it returns                                                  |
|----------------------------------------------|------------------------------------------------------------------|
| `GET /team/{id}/{slug}/overview`             | KD, win rate, rounds played, etc. for the time window            |
| `GET /team/{id}/{slug}/maps`                 | **Per-map CT/T side round winrate** + overall map W/L            |
| `GET /team/{id}/{slug}/matches`              | Match history within the time window                             |
| `GET /team/search?name=...`                  | Resolve a team name to `team_id` + `slug`                        |
| `GET /player/{id}/{slug}`                    | Rating 2.0, KD, HS%, ADR, KPR, DPR for the time window           |
| `GET /rankings`                              | World ranking top 30                                             |
| `GET /matches/upcoming`                      | Upcoming matches with stars, event, time                         |
| `GET /results?offset=0`                      | Recent results (paginate via `offset`)                           |

All time-windowed endpoints accept any of:

- `?start_date=2025-11-20&end_date=2026-04-20`
- `?months_back=5`  (end defaults to today, start = today − 5 months)
- *(none)* — defaults to the last 90 days

## Run it

### Docker (recommended for n8n/Make)

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
```

### Local Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn hltv_parser.api:app --host 0.0.0.0 --port 8000
```

### CLI (good for cron + n8n "Execute Command")

```bash
python cli.py team-maps 4608 natus-vincere --months-back 5
python cli.py rankings
python cli.py player 7998 s1mple --start 2025-11-01 --end 2026-04-01
```

Every CLI command prints a single JSON document to stdout.

## n8n integration

Drop the HTTP Request node:

- **URL** `http://hltv-parser:8000/team/4608/natus-vincere/maps`
  *(use the docker-compose service name, or `host.docker.internal` from
  a desktop n8n)*
- **Query params** `months_back = 5`
- **Headers** `Authorization: Bearer {{$env.HLTV_API_TOKEN}}`
- **Response format** JSON

Then you can reference `{{$json.overall_ct_round_win_percent}}`,
`{{$json.maps[0].ct_round_win_percent}}`, etc. in downstream nodes
(OpenAI for summary, Buffer/Telegram/Notion for publish).

A starter workflow is in [`examples/n8n_workflow.json`](examples/n8n_workflow.json).

## Make.com integration

Use the **HTTP > Make a request** module exactly the same way. See
[`examples/make_blueprint.md`](examples/make_blueprint.md) for field
paths and a sample data flow.

## Authentication

Set `API_TOKEN` in `.env` to require `Authorization: Bearer <token>` on
every request. Leave it empty to disable auth (only safe behind a
private network).

## Be polite

HLTV is behind Cloudflare and rate-limits aggressively. The client:

- Imitates a real Chrome TLS fingerprint via `curl_cffi`.
- Throttles to one request every `HLTV_MIN_DELAY` seconds (default 2.0).
- Retries with exponential backoff on 403/429/5xx.

If you still see `503 hltv_blocked` responses, set `HLTV_PROXY` to a
residential proxy in `.env`.

## Project layout

```
hltv_parser/
├── client.py     # curl_cffi client with throttling + retries
├── parsers.py    # HTML -> dict, defensive selectors
├── service.py    # high-level methods, date-window URL building
├── api.py        # FastAPI app
├── util.py       # date / number helpers
└── __init__.py
cli.py            # JSON-on-stdout CLI
Dockerfile
docker-compose.yml
examples/         # n8n workflow + Make blueprint
```

## Notes & limitations

- HLTV updates its DOM occasionally. The parsers degrade to `null`
  fields rather than crashing — if a field stops returning a value,
  open `parsers.py` and update the CSS selector for that section.
- Scraping HLTV may be against their ToS depending on use. Throttle
  responsibly and consider their official partner program for
  commercial use.
