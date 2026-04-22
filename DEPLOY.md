# Deployment checklist — Render + n8n Cloud

Top-to-bottom. Tick each box before moving to the next; each step
verifies its predecessor. Total wall-clock time on the happy path: ~45
minutes, most of it waiting for Render's first build (~5 min) and
n8n Cloud trial signup.

## 0 — Accounts you need

- [ ] GitHub (free)
- [ ] Render (free) — https://render.com
- [ ] n8n Cloud trial — https://app.n8n.cloud
- [ ] Supabase (free) — https://supabase.com
- [ ] Anthropic console — https://console.anthropic.com (API key)
- [ ] Telegram (already have it)

## 1 — Generate API token (optional — Render can do it for you)

```bash
openssl rand -hex 16
```

Save the value somewhere temporary. You can skip this and let
`render.yaml`'s `generateValue: true` mint one for you on first deploy
— in that case you'll grab it from Render's UI in step 4.

## 2 — Push the repo to your GitHub

```bash
git remote set-url origin git@github.com:<you>/esports-data-api.git
git push -u origin main
```

`render.yaml` is at the repo root. Render needs to read it from your fork.

## 3 — Create the Render service from the blueprint

- [ ] Render dashboard → **New → Blueprint**
- [ ] Connect your GitHub account, pick the fork
- [ ] Render parses `render.yaml` and shows a preview ("1 web service:
      esports-data, plan: free, runtime: docker")
- [ ] Click **Apply**
- [ ] First build takes 4–6 min (Docker layer install)

## 4 — Capture credentials

Render service → **Environment** tab:

- [ ] Copy `API_TOKEN` value (Render generated it)
- [ ] Copy the public URL from the **Settings** tab
      (e.g. `https://esports-data-xxxx.onrender.com`)

## 5 — Smoke-test the public API

```bash
# /health is public — should return immediately even on a cold container
curl https://esports-data-xxxx.onrender.com/health
# → {"ok":true,"sources":["hltv","steam","escharts"]}

# /warmup is also public, used to wake cold containers before real work
curl https://esports-data-xxxx.onrender.com/warmup

# Auth-required endpoint without token → 401
curl -i https://esports-data-xxxx.onrender.com/hltv/rankings
# → HTTP/1.1 401 Unauthorized
# → {"detail":"unauthorized"}

# Auth-required with token → 200 + JSON
curl -H "Authorization: Bearer <API_TOKEN>" \
     https://esports-data-xxxx.onrender.com/hltv/rankings
```

If any of these fail, check Render's **Logs** tab. If you see
`AuthMisconfigured: API_REQUIRE_TOKEN=true but API_TOKEN is empty`,
the env var didn't propagate — re-save `API_TOKEN` in **Environment**
and let Render redeploy.

## 6 — Set up Supabase

- [ ] https://app.supabase.com → **New project**
- [ ] **SQL Editor → New query** → paste
      [`examples/supabase_schema.sql`](examples/supabase_schema.sql) → **Run**
- [ ] **Settings → API** → copy:
  - Project URL → `SUPABASE_URL`
  - `service_role` key → `SUPABASE_SERVICE_ROLE`

## 7 — Sign up for n8n Cloud

- [ ] https://app.n8n.cloud → start the trial
- [ ] Pick the region closest to your Render service (Frankfurt for EU,
      Oregon for US)

## 8 — Set n8n Cloud environment variables

n8n Cloud → **Settings → Variables**:

| Key | Value |
|---|---|
| `ESPORTS_API_BASE` | `https://esports-data-xxxx.onrender.com` (no trailing slash) |
| `TELEGRAM_CHAT_ID` | Your numeric chat id (see step 9) |
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `SUPABASE_URL` | from step 6 |
| `SUPABASE_SERVICE_ROLE` | from step 6 |

## 9 — Create the Telegram bot

- [ ] Telegram → message **@BotFather** → `/newbot` → follow prompts
- [ ] Copy the bot token
- [ ] Send your new bot any message ("hi")
- [ ] Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy `chat.id`
- [ ] Paste `chat.id` into n8n's `TELEGRAM_CHAT_ID` variable

## 10 — Add credentials in n8n Cloud

n8n Cloud → **Credentials → Create credential**:

- [ ] **HTTP Header Auth**, name `parser-api`
  - Header name: `Authorization`
  - Header value: `Bearer <API_TOKEN from step 4>`
- [ ] **Telegram**, name `bot`
  - Bot token from @BotFather
- [ ] *(Optional)* **Supabase**, **Anthropic** if you want to use n8n's
      first-party nodes; the shipped workflow uses raw HTTP for both.

## 11 — Import the Telegram bot workflow

- [ ] **Workflows → Import from File** →
      [`examples/n8n_telegram_bot.json`](examples/n8n_telegram_bot.json)
- [ ] Open the workflow. n8n will warn that some credential references
      can't be resolved — that's expected. For each red node:
  - Telegram nodes → bind to credential `bot`
  - HTTP Request nodes calling the parser → bind to credential `parser-api`
      (already named correctly in the JSON, just confirm)
- [ ] Click **Save**, then toggle **Active** in the top-right
- [ ] In Telegram, type `/rank` to your bot
- [ ] You should get a 5-bullet ranking summary back within ~5s
      (longer on first call after cold start — see next section)

## 12 — Cold-start warmup pattern

Render Free tier scales the parser to zero after ~15 min idle. The
first request to a cold container takes 30–60s. For workflows that
care about latency, prepend a `/warmup` ping:

```
[Schedule Trigger]            ← e.g. cron 0 9 * * *  (daily 09:00)
        │
        ▼
[HTTP Request → /warmup]      ← URL: {{ $env.ESPORTS_API_BASE }}/warmup
        │                       (no auth needed; 1-2s on warm, 30-60s cold)
        ▼
[Wait]                        ← 30 seconds, "Wait" node, "Resume on time interval"
        │
        ▼
[…the rest of your workflow…] ← all subsequent calls hit a warm container
```

The **Wait** node is `n8n-nodes-base.wait`, mode `interval`, value `30`,
unit `seconds`. Add it to any morning-digest workflow before the first
real HTTP call.

For the Telegram bot workflow this is unnecessary (the user is the
trigger and accepts the latency on the first call of the day) — but if
you want zero cold starts, upgrade Render to **Starter** ($7/mo) and
the container stays warm 24/7. Then the warmup nodes can be deleted.

## 13 — Lock down CORS (recommended after step 11 works)

Once the bot is working, tighten the API:

- [ ] Render service → **Environment** → set `ALLOWED_ORIGIN` to your
      n8n Cloud URL: `https://<tenant>.app.n8n.cloud`
- [ ] Save → Render redeploys (~30s)
- [ ] Re-run the smoke tests from step 5 — they should still pass

## 14 — Optional polish

- [ ] **Set up Render alerts** → service → Settings → Notifications
- [ ] **Wire n8n's error workflow** → Settings → Workflows → Error
      Workflow → point at a small workflow that Telegrams you on failure
- [ ] **Bump rate limit** in `render.yaml` (`RATE_LIMIT_PER_MINUTE`) if
      you start fanning out many parallel requests
- [ ] **Add a residential proxy** to `HLTV_PROXY` / `STEAM_PROXY` once
      you start hitting `503 hltv_blocked` — Render's egress IP ages out
      eventually

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `AuthMisconfigured` in Render logs | `API_TOKEN` empty but `API_REQUIRE_TOKEN=true` | Set `API_TOKEN` in Render env, redeploy |
| `401 unauthorized` from n8n | Header Auth credential value missing the `Bearer ` prefix | Edit credential, value must be literally `Bearer <token>` (with the space) |
| First Telegram reply each morning hangs ~60s | Cold start | Add the warmup pattern from step 12, or upgrade to Render Starter |
| `429 Rate limit exceeded` from parser | Workflow fan-out exceeded `RATE_LIMIT_PER_MINUTE` | Raise the env var on Render, or add a Wait node between calls |
| `503 hltv_blocked` | HLTV WAF triggered | Raise `HLTV_MIN_DELAY` to 3.0+ on Render, or set `HLTV_PROXY` |
| Telegram Trigger never fires | n8n Cloud webhook not registered | Toggle workflow Active off/on; n8n Cloud handles the webhook URL automatically (no HTTPS setup needed) |
