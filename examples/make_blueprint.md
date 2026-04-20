# Make.com (Integromat) blueprint

A minimal scenario that fetches a team's CT/T-side winrate over the last
5 months and forwards the JSON to whatever you do next (Notion, Sheets,
ChatGPT, Buffer, etc).

```
[Schedule trigger: every Monday 09:00]
        │
        ▼
[HTTP > Make a request]
   URL:    http://YOUR_HOST:8000/team/4608/natus-vincere/maps
   Method: GET
   Query string:
     months_back = 5
   Headers:
     Authorization = Bearer {{env.HLTV_API_TOKEN}}
   Parse response: Yes (JSON)
        │
        ▼
[OpenAI > Create a completion]   ← summarise data.maps[*]
        │
        ▼
[Buffer / Notion / Sheets / Telegram]
```

Field paths inside the HTTP module's output:

| Use case                                | JSON path                                              |
|-----------------------------------------|--------------------------------------------------------|
| Overall CT round winrate (window)       | `data.overall_ct_round_win_percent`                    |
| Overall T  round winrate (window)       | `data.overall_t_round_win_percent`                     |
| Per-map CT winrate (e.g. Mirage)        | `data.maps[?(@.map=='Mirage')].ct_round_win_percent`   |
| Per-map T  winrate                      | `data.maps[?(@.map=='Mirage')].t_round_win_percent`    |
| Window the request covers               | `data.window.start` / `data.window.end`                |

Repeat the HTTP module per team or wrap it in a Make `Iterator` over a
list of `{team_id, slug}` pairs.
