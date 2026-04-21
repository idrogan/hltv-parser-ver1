# EsportsCharts — weekly viewership digest

Pattern for a Monday-morning "what pulled eyeballs last week" Telegram
post, built on the public tournament leaderboard.

## Flow

```
[Cron: Mondays 09:00]
        │
        ▼
[HTTP > GET /escharts/tournaments?game=cs2]
   URL:     http://esports-data:8000/escharts/tournaments
   Query:   game = cs2
   Headers: Authorization = Bearer {{$env.ESPORTS_API_TOKEN}}
        │
        ▼
[Code / Function node]        ← sort by peak_viewers, take top 5
        │
        ▼
[Supabase > Upsert rows]       ← table: tournament_viewership
        │
        ▼
[Claude API > create-message]
   model: claude-sonnet-4-6
   prompt: "Write a 3-bullet Telegram post. Each bullet: tournament
            name, peak viewers (human-formatted), one angle I could
            develop into a long-form post. Data: {{$json.top5}}"
        │
        ▼
[Telegram > Send message]
```

## What you get back

`GET /escharts/tournaments?game=cs2&year=2025` returns:

```json
{
  "game": "cs2",
  "year": 2025,
  "tournaments": [
    {
      "tournament": "IEM Katowice 2025",
      "url": "https://escharts.com/tournaments/cs2/iem-katowice-2025",
      "slug": "iem-katowice-2025",
      "peak_viewers": 1200000,
      "peak_viewers_text": "1.2M",
      "avg_viewers": 345000,
      "hours_watched": 42000000,
      "airtime_hours": 120.5
    },
    ...
  ]
}
```

## Single-tournament drilldown

```
GET /escharts/tournament/cs2/iem-katowice-2025
```

Returns the same headline stats plus a per-channel breakdown so you
can spot which streamer/language/network hosted the audience — very
useful for outreach angles.

## Suggested Supabase schema

```sql
create table tournament_viewership (
  slug              text,
  game              text,
  tournament        text,
  peak_viewers      bigint,
  avg_viewers       bigint,
  hours_watched     bigint,
  airtime_hours     numeric,
  fetched_at        timestamptz default now(),
  primary key (slug, fetched_at)
);
```

Storing it with `fetched_at` means you can diff week-over-week and
catch "X tournament grew 40% YoY" angles automatically.
