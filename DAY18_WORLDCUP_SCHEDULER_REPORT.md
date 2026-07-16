# Day 18. MCP Scheduler + Background World Cup Summary

## Summary

- Remote MCP server runs 24/7 under systemd.
- Background scheduler fetches FIFA World Cup scoreboard data periodically.
- Server stores snapshots in JSON.
- MCP tool returns aggregated match summary.
- Agent sends MCP result to DeepSeek and gets a human summary.

## Remote MCP

- url: `http://138.16.168.37:8002/mcp`
- connected: `True`
- generated_at: `2026-06-25T18:49:09+00:00`
- tools_count: `1`
- expected_tool_present: `True`

## Tool Schema

```json
[
  {
    "name": "get_worldcup_match_summary",
    "description": "Return the latest aggregated FIFA World Cup match summary collected by the server background scheduler. The server stores snapshots in JSON and refreshes them periodically.",
    "input_schema": {
      "properties": {
        "include_matches": {
          "default": true,
          "description": "When true, include normalized match list from stored scheduler snapshot.",
          "title": "Include Matches",
          "type": "boolean"
        },
        "force_refresh": {
          "default": false,
          "description": "When true, fetch ESPN data now before returning the aggregated summary.",
          "title": "Force Refresh",
          "type": "boolean"
        }
      },
      "title": "get_worldcup_match_summaryArguments",
      "type": "object"
    }
  }
]
```

## Scheduled Tool Result

```json
{
  "scheduler": {
    "enabled": true,
    "refresh_seconds": 300,
    "store_path": "/opt/day18-worldcup-mcp/data/worldcup_summary.json",
    "last_fetched_at": "2026-06-25T18:49:05+00:00",
    "force_refresh_used": true
  },
  "source": "espn_scoreboard_api",
  "source_url": "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
  "aggregate": {
    "total_matches": 18,
    "completed_matches": 6,
    "live_matches": 0,
    "scheduled_matches": 12,
    "latest_results": [
      "Bosnia-Herzegovina 3 - 1 Qatar",
      "Switzerland 2 - 1 Canada",
      "Morocco 4 - 2 Haiti",
      "Scotland 0 - 3 Brazil",
      "Czechia 0 - 3 Mexico",
      "South Africa 1 - 0 South Korea"
    ],
    "live_now": [],
    "next_matches": [
      {
        "date": "2026-06-25T20:00Z",
        "match": "Ivory Coast at Curaçao",
        "status": "Thu, June 25th at 4:00 PM EDT",
        "venue": "Lincoln Financial Field",
        "group": "FIFA World Cup, Group E"
      },
      {
        "date": "2026-06-25T20:00Z",
        "match": "Germany at Ecuador",
        "status": "Thu, June 25th at 4:00 PM EDT",
        "venue": "MetLife Stadium",
        "group": "FIFA World Cup, Group E"
      },
      {
        "date": "2026-06-25T23:00Z",
        "match": "Sweden at Japan",
        "status": "Thu, June 25th at 7:00 PM EDT",
        "venue": "AT&T Stadium",
        "group": "FIFA World Cup, Group F"
      },
      {
        "date": "2026-06-25T23:00Z",
        "match": "Netherlands at Tunisia",
        "status": "Thu, June 25th at 7:00 PM EDT",
        "venue": "GEHA Field at Arrowhead Stadium",
        "group": "FIFA World Cup, Group F"
      },
      {
        "date": "2026-06-26T02:00Z",
        "match": "Australia at Paraguay",
        "status": "Thu, June 25th at 10:00 PM EDT",
        "venue": "Levi's Stadium",
        "group": "FIFA World Cup, Group D"
      },
      {
        "date": "2026-06-26T02:00Z",
        "match": "United States at Türkiye",
        "status": "Thu, June 25th at 10:00 PM EDT",
        "venue": "SoFi Stadium",
        "group": "FIFA World Cup, Group D"
      },
      {
        "date": "2026-06-26T19:00Z",
        "match": "France at Norway",
        "status": "Fri, June 26th at 3:00 PM EDT",
        "venue": "Gillette Stadium",
        "group": "FIFA World Cup, Group I"
      },
      {
        "date": "2026-06-26T19:00Z",
        "match": "Iraq at Senegal",
        "status": "Fri, June 26th at 3:00 PM EDT",
        "venue": "BMO Field",
        "group": "FIFA World Cup, Group I"
      }
    ]
  }
}
```

## DeepSeek Summary

- error: `none`
- tokens: prompt=964, completion=335, total=1299

```text
Вот сводка по ЧМ-2026 на основе данных:

**✅ Уже сыграно (6 матчей):**
- Босния 3-1 Катар
- Швейцария 2-1 Канада
- Марокко 4-2 Гаити
- Шотландия 0-3 Бразилия
- Чехия 0-3 Мексика
- ЮАР 1-0 Южная Корея

**🔴 LIVE:** Прямых эфиров нет.

**📅 Ближайшие матчи (сегодня-завтра):**
- **Сегодня, 23:00 МСК:** Кот-д'Ивуар — Кюрасао (Группа E), Германия — Эквадор (Группа E)
- **Завтра, 02:00 МСК:** Швеция — Япония (Группа F), Нидерланды — Тунис (Группа F)
- **Завтра, 05:00 МСК:** Австралия — Парагвай (Группа D), США — Турция (Группа D)
- **26 июня, 22:00 МСК:** Франция — Норвегия (Группа I), Ирак — Сенегал (Группа I)

**🔥 Важно:** Сегодня топ-матчи — Германия vs Эквадор и Нидерланды vs Тунис. Завтра ночью — США vs Турция.
```

## How To Check

```powershell
.\.venv\Scripts\python.exe day18_worldcup_agent.py tools --url http://138.16.168.37:8002/mcp
.\.venv\Scripts\python.exe day18_worldcup_agent.py summary --force-refresh --url http://138.16.168.37:8002/mcp
.\.venv\Scripts\python.exe day18_worldcup_agent.py ask --force-refresh --url http://138.16.168.37:8002/mcp
```