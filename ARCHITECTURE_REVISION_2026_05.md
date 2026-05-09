# Architecture revision — 2026-05

Status: draft, awaiting review. Do not implement until approved.

This is the post-Phase-1 architectural review. Phase 1 verified that
PandaScore writes 445 rows in 9 seconds end-to-end into Supabase. HLTV
is paused. Liquipedia is gated on LiquipediaDB API approval. With that
baseline real, this document covers the six items from the original
brief: HLTV pause/resume, coverage gap, staleness handling, failure
modes, cost, and deduplication.

The bias of every decision below is **"keep the smallest thing that
works."** The pipeline went a year without writing a row; the goal now
is to keep it writing rows reliably, not to perfect the schema.

---

## 1. Pause / resume protocol for HLTV

**Pause is already in place** (commit `a319c5c`):

* `HLTV_ENABLED=false` is the default. Every public method on
  `HLTVService`, the FastAPI router, and the CLI short-circuits with a
  structured `event=hltv_paused` log line and an
  `HLTVPausedError` → 503 / exit code 3.
* Code, fixtures, tests, env vars stay in the repo. No deletions.
* The live n8n workflow on the DigitalOcean droplet that triggered the
  HLTV digest needs to be deactivated **manually** — it's not in the
  repo so the pause flag can't reach it. Verify it's off before merging
  the pause commit to production.

**Resume protocol** lives at [`docs/RESUMING_HLTV.md`](docs/RESUMING_HLTV.md).
The summary: flip the flag, wire a paid bypass (Scrapfly / iProyal /
ScrapeOps), run a one-shot smoke test against `/team` + `/rank` (the
two endpoints that worked unstably before), then re-enable the n8n
schedule. All gated on hitting `/stats` successfully **three times in
a row** before re-activating any cron.

**Re-enabling without a paid bypass is forbidden.** Cloudflare-block
behaviour tripled HLTV's anti-bot strictness in early May 2026; even
careful curl_cffi + 2-second throttle hits 502 within minutes. The
flag exists precisely to make "let's just try without budget" hard.

---

## 2. Coverage gap during the HLTV pause

The honest list of things HLTV gives that nothing else free does:

| HLTV signal | Free substitute? | Notes |
|---|---|---|
| Player rating 2.0 | **None.** PandaScore has K/D and HS% per-match but not the proprietary rating. | Liquipedia carries it on player pages but inconsistently. |
| Demo files (.dem) | **None.** | Demos are the source of round-economy and clutch metrics. Without them the entire "tactical analysis" content angle is dead until HLTV resumes. |
| Round economy (eco/force/full-buy outcomes) | **None.** | Derived from demos. |
| Team CT / T side winrate per map (windowed) | Partial. PandaScore has match-level scores, you can derive map winrate but not side splits. | Side splits are an HLTV-specific aggregation. |
| World ranking (HLTV top 30) | **None official.** ESL ranking exists but isn't broadly cited. | Use HLTV ranking as historical only; current ranking content waits. |
| Pre-match team form (last N matches) | PandaScore covers this. | Already in our `matches` table. |
| Tournament prize distribution by place | Liquipedia (when our key arrives). | Not blocked once Liquipedia is wired. |
| Tournament dates / location / prize pool | PandaScore covers this. | Already in our `tournaments` table. |

**Don't fake substitutes.** Specifically: do not invent a "rating-like"
score from PandaScore data and present it as comparable to HLTV's
rating 2.0. Either show the real signal or skip the angle.

The content implication: while paused, lean on **schedule, results,
team meta, and (later) Steam Market signals**. Save the deep tactical
content for when HLTV resumes.

---

## 3. Staleness handling for HLTV-fed tables

The schema HLTV would write into (`hltv_team_overview`,
`hltv_team_map_stats`, `hltv_rankings`, `hltv_player_snapshots`,
`hltv_results`) lives in `examples/supabase_schema.sql` — **never
applied** to the live database. So strictly speaking nothing is stale
right now, because nothing has been written.

**Decision:** when we resume HLTV, every HLTV table gets a
`fetched_at timestamptz` column (most already have it in the
aspirational schema) and queries that consume them must check
`fetched_at > now() - interval '7 days'` before quoting numbers. We
do not bake "warning banners" into the data — the warning lives in
the consumer (n8n / Claude prompt template) where it can be tuned per
content angle.

**Until resume:** the `hltv_*` tables don't exist, so consumers can't
accidentally read stale data. Keeping the aspirational schema in
`examples/` rather than `migrations/` is intentional — it must not be
applied automatically.

---

## 4. Failure modes catalog

One row per mode. "Who notices" is who finds out *first*, not who
ultimately fixes it.

| # | Failure | Detection | Mitigation | Who notices |
|---|---|---|---|---|
| F1 | Liquipedia 429 / temp IP ban | `LiquipediaRateLimited` raised, `_scraper_runs.status='error'`, error column has the response | Honor `Retry-After`; back off the run schedule from 6h to 24h until recovery; per-day cache absorbs duplicate intra-day re-runs | Whoever reads `_scraper_runs` (manual today; Phase 3 alert) |
| F2 | PandaScore quota near monthly cap | `client.rate_limit_remaining` persisted in `_scraper_runs.meta`; soft-stop kicks in when remaining < 10% of limit | Cut `--matches-pages` and `--tournaments-pages` defaults; switch matches window from 7d to 3d; only "upcoming" pulls every 30 min, the rest 6h | F2 surfaces in `_scraper_runs.meta.rate_limit_remaining` trending down |
| F3 | PandaScore returns no `X-Rate-Limit-Limit` header (current state) | Soft-stop never triggers because the ratio can't be computed | Switch to absolute threshold: stop if `remaining < 50` regardless of limit. One-line fix in `pandascore_parser/client.py` | Silent — needs us to actually look |
| F4 | Steam Market HTML / JSON shape changes | `steam_market` parser raises; `_scraper_runs` row goes `error`; sample of failed `market_hash_name` in error column | Pin a known-good fixture; if Steam changes, parse against fixture first to localize the diff. No bulk re-fetch on retry — waste of throttle budget | Same — `_scraper_runs` |
| F5 | Steam soft-blocks the IP (rate cap) | 429 on `/market/...`; we already throttle 3.5s but Steam can still cap | Increase `STEAM_MIN_DELAY` to 5.0; halve concurrent items per run; consider rotating UA | n8n returns 503 → workflow run fails |
| F6 | Supabase down / unreachable | `SupabaseWriteError` from the writer; `_scraper_runs` row never finishes (status stays `running`) | Retry with backoff; one staleness alert: any `running` row older than 1 hour = bug | A `running` row that doesn't finish is the loud signal |
| F7 | DigitalOcean droplet down (n8n host) | No `_scraper_runs` rows for any scraper for > 1 schedule interval | Phase 3 cron-ping (cheap external uptime check) → email | No-data is the loudest signal we don't have today |
| F8 | Service-role key leaked | Supabase audit log shows writes from unknown IP | Rotate immediately in Supabase → Project Settings → API → "regenerate service_role"; redeploy with new key. Service role bypasses RLS by design — it's the most sensitive secret in the stack | Whoever notices weird writes |
| F9 | n8n workflow definition lost (droplet rebuild without volume) | Schedule triggers stop firing | Workflows that matter are also committed to `examples/n8n_workflow.json`. Rebuild from there | Same as F7 |
| F10 | Liquipedia application denied or revoked | `cargoquery()` keeps raising even with key set | Fallback: `action=parse` on individual major pages (slow, 1/30s). For the stickers content angle this is enough | F10 surfaces on first scheduled liquipedia run after revocation |

The pattern across F1–F6: `_scraper_runs` is the single source of
truth. Everything writes to it; everything is observable from one
table. Phase 3 should add a 5-line SQL view (`v_pipeline_health`) that
surfaces "any scraper stuck in `running`", "any scraper with `error`
status in the last hour", and "remaining quota by scraper".

---

## 5. Cost re-estimate

| Line item | Today | Trend |
|---|---|---|
| DigitalOcean droplet (n8n host) | $6–12/mo (basic) | Unchanged. n8n is the only running service on it. |
| Supabase | $0 (free tier) | At 445 rows per pandascore run × 4 runs/day, that's < 2k rows/day. Free tier holds 500 MB. We hit it in months, not weeks. |
| PandaScore | $0 | Free tier ~10 000 req/month. Today's run cost ~6 requests; 4 runs/day = ~720/mo. Plenty of headroom. |
| Liquipedia | $0 (free tier, app-gated) | 60 req/h cap is the binding constraint. One run / 6h is well under. |
| Steam Market | $0 | Throttled politely; no API key needed. |
| Scrapfly / iProyal | **$0 (paused with HLTV)** | Brings HLTV cost to ~$15–30/mo when re-enabled. Out of budget today. |
| Anthropic API | **$0 (disabled)** | Code path stays. No scheduled job calls Claude until budget allows. |
| Total active | **~$6–12/mo** | DigitalOcean droplet only. |
| Total when HLTV resumes | ~$25–40/mo | Adds Scrapfly, possibly residential proxy. |

**The bottleneck is not money, it's API approval cycles** (Liquipedia)
and **content production cadence** (writing the post matters more than
adding a fourth source).

---

## 6. Deduplication strategy

This was deliberately deferred in Phase 1. Now's the time to specify
it. The problem: the same Major can land in `tournaments` once from
PandaScore (`source_id="20710"`, name `"IEM — Cologne Major 2026 —
Playoffs"`) and once from Liquipedia (`source_id="IEM_Cologne_2026"`,
name `"IEM Cologne 2026"`). Currently they're two unrelated rows.

### Recommended approach: self-referential `canonical_id`

Add one nullable column per relevant table, no new tables:

```sql
alter table tournaments add column canonical_id bigint
    references tournaments(id) on delete set null;
alter table matches add column canonical_id bigint
    references matches(id) on delete set null;
```

Semantics:

* `canonical_id IS NULL` → this row hasn't been deduped yet.
* `canonical_id = id` → this row is the canonical representative
  (chosen winner of a dedup cluster).
* `canonical_id = some_other_id` → this row is a non-canonical alias
  pointing at the canonical representative.

Querying canonical-only is then:

```sql
-- "give me one row per canonical tournament"
select * from tournaments
where canonical_id is null or canonical_id = id;
```

### Matching rules (run as a periodic job, not inline)

A separate `dedup` job runs nightly. For each new pair `(t1, t2)` of
tournaments from different sources:

* If `lower(t1.name)` is contained in `lower(t2.name)` (or vice versa)
  AND the start_date is within ±2 days: **probable match**.
* If both `start_date` and `end_date` match exactly AND both have a
  prize_pool_usd that agrees within ±5%: **strong match**.
* Else: leave alone.

The job picks the row with the most filled-in columns as the canonical,
sets `canonical_id` accordingly on both. Liquipedia rows tend to win
because they carry richer location and tier data; PandaScore rows
become aliases.

For matches, the natural key is `(scheduled_at ± 30 min, sorted([team_a,
team_b]))`. Same logic, same column.

### Why not a `canonical_tournaments` table?

Considered. Rejected because:
* It doubles the joins for every consumer query.
* PostgREST isn't great at recursive joins.
* The self-referential pattern is a single ALTER and one nightly job;
  the table-based pattern is a migration plus a join in every query
  path. We don't yet know what the queries look like.

If multi-tenant consumers ever need different "canonical opinions"
(e.g. content team prefers Liquipedia, analytics team prefers
PandaScore), the table-based pattern is the right move and we migrate
then. Today YAGNI.

### Implementation order (post-approval)

1. Migration `0002_add_canonical_id.sql` — add the nullable column +
   index. Reversible. Backwards compatible (existing queries ignore
   the column).
2. `common/dedup.py` — pure-function matchers + a `run_dedup()` entry
   point that scans pairs and writes `canonical_id`. No external
   deps.
3. CLI: `python cli.py dedup run`. Schedule once a day in n8n.
4. Update one query in PandaScore / Liquipedia runners (if any)
   that needs canonical-aware reads. None do today; consumers
   downstream (Telegram bot, content briefings) are where this
   matters.

Estimated work: half a day, almost all in `common/dedup.py`. Defer
until after the first real content post is shipped — concrete reader
queries will sharpen the matching rules.

---

## What this document does NOT cover

* **Ranking / leaderboard layer.** Out of scope. HLTV's ranking is
  the one we'd quote and HLTV is paused.
* **Real-time / streaming.** Phase 1 is batch-only and that fits the
  content cadence (one post per few days). Realtime adds infra
  surface for no clear win.
* **Multi-game.** The schema (`game` columns absent, source enum CS
  specific) is single-game on purpose. Generalising waits until a
  second game has demonstrated content value.

---

## Approval checklist

Tick before implementing:

- [ ] Pause/resume protocol §1 + checklist `docs/RESUMING_HLTV.md` is correct
- [ ] Coverage-gap §2 honesty: are we OK shipping content without HLTV-only signals?
- [ ] Staleness §3 decision: `fetched_at` checked at consumer, no banners in data
- [ ] Failure-modes §4: anything missing? F8 (key leak) is the highest-stakes one
- [ ] Cost §5 numbers: still within "no paid services this iteration"
- [ ] Dedup §6: self-referential `canonical_id` over a separate table
- [ ] Implementation order §6: defer until after first real post

When all six are green, we either:
(a) start Phase 3 polish (tests, README, CLAUDE.md), or
(b) skip Phase 3 and write the actual post.

The brief calls Phase 3 "optional polish (only if owner asks)".
Default: skip it and write the post.
