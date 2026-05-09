# Resuming HLTV

The HLTV scraper is paused via `HLTV_ENABLED=false` (default) since
2026-05-08. HLTV's anti-bot blocks `/stats` and team overview pages
with 502 / Cloudflare challenges within minutes of unthrottled
access; even `curl_cffi` + 2-second throttle fails. Re-enabling
without a paid Cloudflare bypass will get the IP banned and waste
quota — see the lock at the end of this document.

## Pre-flight (do these before flipping the flag)

1. **Budget approved for Cloudflare bypass.** Pick one:
   - **Scrapfly** — easiest. Add `SCRAPFLY_API_KEY` to `.env`.
     Wrap `HLTVClient.get` calls with `client.scrape(url, asp=True,
     render_js=False)`. Free tier exists but is limited; budget
     ~$15–30/mo for daily scrapes.
   - **iProyal residential proxy** — set `HLTV_PROXY=http://user:pass@host:port`.
     Cheaper if you already have a subscription, but no built-in
     anti-bot — relies on residential IP being rotated.
   - **ScrapeOps + their proxy** — middle ground.

2. **Smoke-test the bypass on `/team/4608/natus-vincere`** *before*
   touching the schedule. Run:
   ```bash
   HLTV_ENABLED=true python cli.py hltv team 4608 natus-vincere --months-back 3
   ```
   Expect a populated JSON. If it returns `{"error": "hltv_blocked"}`,
   the bypass isn't working — do NOT proceed.

3. **Three consecutive successful runs** of a deeper page
   (`/stats/teams/maps/4608/natus-vincere`) over 30 minutes apart.
   Anti-bot warming-up matters; one success ≠ stable.

4. **Apply the HLTV-side schema additions** (currently still in
   `examples/supabase_schema.sql`, never applied). Specifically the
   tables `hltv_team_overview`, `hltv_team_map_stats`, `hltv_rankings`,
   `hltv_player_snapshots`, `hltv_results`. Move them into a real
   `migrations/0003_hltv_resume.sql` first; do not paste from
   `examples/`.

## Flipping the flag

5. Set in production env (Render / DO droplet):
   ```
   HLTV_ENABLED=true
   ```

6. Update n8n on the DigitalOcean droplet:
   - Re-activate the workflow `HLTV - Weekly CT-side winrate digest`
     (currently deactivated; the `examples/n8n_workflow.json` file is
     the authoritative copy).
   - **Set its schedule to weekly Monday 09:00 first.** Do not run it
     hourly until 7 days of weekly runs all green in `_scraper_runs`.

## Verification (within 24 hours of re-enabling)

7. `select * from _scraper_runs where scraper_name='hltv' order by
   id desc limit 10;` — every row should be `status='ok'`. Any
   `error` row halts re-activation; investigate before continuing.

8. Spot-check one row in `hltv_team_map_stats` — does the CT/T
   winrate look sane vs the live HLTV page? If numbers diverge, the
   bypass is rendering an A/B variant or stale cached page; this has
   happened before with Scrapfly.

## Lock — do not bypass

9. Re-enabling without §1 (paid bypass approved) is forbidden. The
   next IP ban from HLTV affects the whole droplet, including
   PandaScore / Steam scrapes that piggyback on the same egress.
   The `HLTV_ENABLED` flag exists precisely to make "let's just try
   without budget" hard — keep it default-false.

10. Once re-enabled and stable, update `RECON.md` and
    `ARCHITECTURE_REVISION_2026_05.md` to reflect that HLTV is back,
    and remove this lock section.
