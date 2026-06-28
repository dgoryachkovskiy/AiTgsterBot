# Day 20. Orchestration MCP

## Summary

- Agent registers multiple remote MCP servers.
- Agent builds one tool catalog and routes a long flow across servers.
- Flow uses GitHub MCP, World Cup scheduler MCP, and World Cup pipeline MCP.

## Status

- route_source: `deepseek`
- route_ok: `True`
- order_ok: `True`
- all_steps_ok: `True`
- saved_json: `/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260628T084556Z-95b0ef82.json`
- saved_markdown: `/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260628T084556Z-95b0ef82.md`

## Servers

```json
[
  {
    "server_id": "github",
    "server_url": "http://138.16.168.37:8000/mcp",
    "connected": true,
    "tools_count": 1,
    "error": ""
  },
  {
    "server_id": "worldcup_scheduler",
    "server_url": "http://138.16.168.37:8002/mcp",
    "connected": true,
    "tools_count": 1,
    "error": ""
  },
  {
    "server_id": "worldcup_pipeline",
    "server_url": "http://138.16.168.37:8004/mcp",
    "connected": true,
    "tools_count": 4,
    "error": ""
  }
]
```

## Route

```json
[
  {
    "server_id": "github",
    "tool_name": "get_github_repo_summary",
    "arguments": {
      "owner": "python",
      "repo": "cpython"
    },
    "reason": "Получить сводку репозитория python/cpython для отчёта."
  },
  {
    "server_id": "worldcup_scheduler",
    "tool_name": "get_worldcup_match_summary",
    "arguments": {
      "include_matches": true,
      "force_refresh": true
    },
    "reason": "Получить текущую сводку матчей ЧМ с принудительным обновлением."
  },
  {
    "server_id": "worldcup_pipeline",
    "tool_name": "run_worldcup_pipeline",
    "arguments": {
      "force_refresh": true,
      "limit": 50,
      "style": "short_ru",
      "filename_prefix": "worldcup_pipeline"
    },
    "reason": "Запустить полный пайплайн ЧМ: поиск, суммаризация и сохранение в файлы."
  }
]
```

## Step Results

```json
[
  {
    "step": 1,
    "server_id": "github",
    "server_url": "http://138.16.168.37:8000/mcp",
    "tool_name": "get_github_repo_summary",
    "arguments": {
      "owner": "python",
      "repo": "cpython"
    },
    "ok": true,
    "elapsed_seconds": 0.9734118999913335,
    "result": {
      "source": "github_public_api",
      "owner": "python",
      "repo": "cpython",
      "full_name": "python/cpython",
      "name": "cpython",
      "description": "The Python programming language",
      "stars": 73543,
      "forks": 34787,
      "open_issues": 9440,
      "language": "Python",
      "license": "NOASSERTION",
      "updated_at": "2026-06-28T08:43:46Z",
      "html_url": "https://github.com/python/cpython"
    },
    "error": ""
  },
  {
    "step": 2,
    "server_id": "worldcup_scheduler",
    "server_url": "http://138.16.168.37:8002/mcp",
    "tool_name": "get_worldcup_match_summary",
    "arguments": {
      "include_matches": true,
      "force_refresh": true
    },
    "ok": true,
    "elapsed_seconds": 1.735080200014636,
    "result": {
      "scheduler": {
        "enabled": true,
        "refresh_seconds": 7200,
        "store_path": "/opt/day18-worldcup-mcp/data/worldcup_summary.json",
        "last_fetched_at": "2026-06-28T08:45:55+00:00",
        "force_refresh_used": true
      },
      "source": "espn_scoreboard_api",
      "source_url": "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
      "aggregate": {
        "total_matches": 10,
        "completed_matches": 6,
        "live_matches": 0,
        "scheduled_matches": 4,
        "latest_results": [
          "Croatia 2 - 1 Ghana",
          "Panama 0 - 2 England",
          "Colombia 0 - 0 Portugal",
          "Congo DR 3 - 1 Uzbekistan",
          "Algeria 3 - 3 Austria",
          "Jordan 1 - 3 Argentina"
        ],
        "live_now": [],
        "next_matches": [
          {
            "date": "2026-06-28T19:00Z",
            "match": "Canada at South Africa",
            "status": "Sun, June 28th at 3:00 PM EDT",
            "venue": "SoFi Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-29T17:00Z",
            "match": "Japan at Brazil",
            "status": "Mon, June 29th at 1:00 PM EDT",
            "venue": "NRG Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-29T20:30Z",
            "match": "Paraguay at Germany",
            "status": "Mon, June 29th at 4:30 PM EDT",
            "venue": "Gillette Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-30T01:00Z",
            "match": "Morocco at Netherlands",
            "status": "Mon, June 29th at 9:00 PM EDT",
            "venue": "Estadio BBVA",
            "group": "FIFA World Cup, Round of 32"
          }
        ]
      },
      "matches": [
        {
          "id": "760480",
          "name": "Ghana at Croatia",
          "short_name": "GHA @ CRO",
          "date": "2026-06-27T21:00Z",
          "stage": "group-stage",
          "group": "FIFA World Cup, Group L",
          "status_state": "post",
          "status": "Full Time",
          "status_detail": "FT",
          "completed": true,
          "venue": "Lincoln Financial Field",
          "city": "Philadelphia, Pennsylvania",
          "teams": [
            {
              "home_away": "home",
              "name": "Croatia",
              "abbreviation": "CRO",
              "score": "2",
              "winner": true
            },
            {
              "home_away": "away",
              "name": "Ghana",
              "abbreviation": "GHA",
              "score": "1",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760480/ghana-croatia"
        },
        {
          "id": "760485",
          "name": "England at Panama",
          "short_name": "ENG @ PAN",
          "date": "2026-06-27T21:00Z",
          "stage": "group-stage",
          "group": "FIFA World Cup, Group L",
          "status_state": "post",
          "status": "Full Time",
          "status_detail": "FT",
          "completed": true,
          "venue": "MetLife Stadium",
          "city": "East Rutherford, New Jersey",
          "teams": [
            {
              "home_away": "home",
              "name": "Panama",
              "abbreviation": "PAN",
              "score": "0",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "England",
              "abbreviation": "ENG",
              "score": "2",
              "winner": true
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760485/england-panama"
        },
        {
          "id": "760481",
          "name": "Portugal at Colombia",
          "short_name": "POR @ COL",
          "date": "2026-06-27T23:30Z",
          "stage": "group-stage",
          "group": "FIFA World Cup, Group K",
          "status_state": "post",
          "status": "Full Time",
          "status_detail": "FT",
          "completed": true,
          "venue": "Hard Rock Stadium",
          "city": "Miami Gardens, Florida",
          "teams": [
            {
              "home_away": "home",
              "name": "Colombia",
              "abbreviation": "COL",
              "score": "0",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Portugal",
              "abbreviation": "POR",
              "score": "0",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760481/portugal-colombia"
        },
        {
          "id": "760482",
          "name": "Uzbekistan at Congo DR",
          "short_name": "UZB @ COD",
          "date": "2026-06-27T23:30Z",
          "stage": "group-stage",
          "group": "FIFA World Cup, Group K",
          "status_state": "post",
          "status": "Full Time",
          "status_detail": "FT",
          "completed": true,
          "venue": "Mercedes-Benz Stadium",
          "city": "Atlanta, Georgia",
          "teams": [
            {
              "home_away": "home",
              "name": "Congo DR",
              "abbreviation": "COD",
              "score": "3",
              "winner": true
            },
            {
              "home_away": "away",
              "name": "Uzbekistan",
              "abbreviation": "UZB",
              "score": "1",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760482/uzbekistan-congo-dr"
        },
        {
          "id": "760484",
          "name": "Austria at Algeria",
          "short_name": "AUT @ ALG",
          "date": "2026-06-28T02:00Z",
          "stage": "group-stage",
          "group": "FIFA World Cup, Group J",
          "status_state": "post",
          "status": "Full Time",
          "status_detail": "FT",
          "completed": true,
          "venue": "GEHA Field at Arrowhead Stadium",
          "city": "Kansas City, Missouri",
          "teams": [
            {
              "home_away": "home",
              "name": "Algeria",
              "abbreviation": "ALG",
              "score": "3",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Austria",
              "abbreviation": "AUT",
              "score": "3",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760484/austria-algeria"
        },
        {
          "id": "760483",
          "name": "Argentina at Jordan",
          "short_name": "ARG @ JOR",
          "date": "2026-06-28T02:00Z",
          "stage": "group-stage",
          "group": "FIFA World Cup, Group J",
          "status_state": "post",
          "status": "Full Time",
          "status_detail": "FT",
          "completed": true,
          "venue": "AT&T Stadium",
          "city": "Arlington, Texas",
          "teams": [
            {
              "home_away": "home",
              "name": "Jordan",
              "abbreviation": "JOR",
              "score": "1",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Argentina",
              "abbreviation": "ARG",
              "score": "3",
              "winner": true
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760483/argentina-jordan"
        },
        {
          "id": "760486",
          "name": "Canada at South Africa",
          "short_name": "CAN @ RSA",
          "date": "2026-06-28T19:00Z",
          "stage": "round-of-32",
          "group": "FIFA World Cup, Round of 32",
          "status_state": "pre",
          "status": "Scheduled",
          "status_detail": "Sun, June 28th at 3:00 PM EDT",
          "completed": false,
          "venue": "SoFi Stadium",
          "city": "Inglewood, California",
          "teams": [
            {
              "home_away": "home",
              "name": "South Africa",
              "abbreviation": "RSA",
              "score": "0",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Canada",
              "abbreviation": "CAN",
              "score": "0",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760486/canada-south-africa"
        },
        {
          "id": "760487",
          "name": "Japan at Brazil",
          "short_name": "JPN @ BRA",
          "date": "2026-06-29T17:00Z",
          "stage": "round-of-32",
          "group": "FIFA World Cup, Round of 32",
          "status_state": "pre",
          "status": "Scheduled",
          "status_detail": "Mon, June 29th at 1:00 PM EDT",
          "completed": false,
          "venue": "NRG Stadium",
          "city": "Houston, Texas",
          "teams": [
            {
              "home_away": "home",
              "name": "Brazil",
              "abbreviation": "BRA",
              "score": "0",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Japan",
              "abbreviation": "JPN",
              "score": "0",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760487/japan-brazil"
        },
        {
          "id": "760489",
          "name": "Paraguay at Germany",
          "short_name": "PAR @ GER",
          "date": "2026-06-29T20:30Z",
          "stage": "round-of-32",
          "group": "FIFA World Cup, Round of 32",
          "status_state": "pre",
          "status": "Scheduled",
          "status_detail": "Mon, June 29th at 4:30 PM EDT",
          "completed": false,
          "venue": "Gillette Stadium",
          "city": "Foxborough, Massachusetts",
          "teams": [
            {
              "home_away": "home",
              "name": "Germany",
              "abbreviation": "GER",
              "score": "0",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Paraguay",
              "abbreviation": "PAR",
              "score": "0",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760489/paraguay-germany"
        },
        {
          "id": "760488",
          "name": "Morocco at Netherlands",
          "short_name": "MAR @ NED",
          "date": "2026-06-30T01:00Z",
          "stage": "round-of-32",
          "group": "FIFA World Cup, Round of 32",
          "status_state": "pre",
          "status": "Scheduled",
          "status_detail": "Mon, June 29th at 9:00 PM EDT",
          "completed": false,
          "venue": "Estadio BBVA",
          "city": "Guadalupe",
          "teams": [
            {
              "home_away": "home",
              "name": "Netherlands",
              "abbreviation": "NED",
              "score": "0",
              "winner": false
            },
            {
              "home_away": "away",
              "name": "Morocco",
              "abbreviation": "MAR",
              "score": "0",
              "winner": false
            }
          ],
          "link": "https://www.espn.com/soccer/match/_/gameId/760488/morocco-netherlands"
        }
      ]
    },
    "error": ""
  },
  {
    "step": 3,
    "server_id": "worldcup_pipeline",
    "server_url": "http://138.16.168.37:8004/mcp",
    "tool_name": "run_worldcup_pipeline",
    "arguments": {
      "force_refresh": true,
      "limit": 50,
      "style": "short_ru",
      "filename_prefix": "worldcup_pipeline"
    },
    "ok": true,
    "elapsed_seconds": 1.398688499815762,
    "result": {
      "pipeline_id": "20260628T084556Z-95b0ef82",
      "step": "run_worldcup_pipeline",
      "source_url": "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
      "automatic_chain": [
        "search_worldcup_matches",
        "summarize_worldcup_matches",
        "save_worldcup_summary"
      ],
      "search": {
        "pipeline_id": "20260628T084556Z-95b0ef82",
        "step": "search_worldcup_matches",
        "source": "espn_scoreboard_api",
        "source_url": "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
        "fetched_at": "2026-06-28T08:45:56+00:00",
        "requested_days": [
          "2026-06-27",
          "2026-06-28",
          "2026-06-29",
          "2026-06-30",
          "2026-07-01",
          "2026-07-02",
          "2026-07-03"
        ],
        "query": "",
        "limit": 50,
        "matches": [
          {
            "id": "760480",
            "name": "Ghana at Croatia",
            "short_name": "GHA @ CRO",
            "date": "2026-06-27T21:00Z",
            "stage": "group-stage",
            "group": "FIFA World Cup, Group L",
            "status_state": "post",
            "status": "Full Time",
            "status_detail": "FT",
            "completed": true,
            "venue": "Lincoln Financial Field",
            "city": "Philadelphia, Pennsylvania",
            "teams": [
              {
                "home_away": "home",
                "name": "Croatia",
                "abbreviation": "CRO",
                "score": "2",
                "winner": true
              },
              {
                "home_away": "away",
                "name": "Ghana",
                "abbreviation": "GHA",
                "score": "1",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760480/ghana-croatia"
          },
          {
            "id": "760485",
            "name": "England at Panama",
            "short_name": "ENG @ PAN",
            "date": "2026-06-27T21:00Z",
            "stage": "group-stage",
            "group": "FIFA World Cup, Group L",
            "status_state": "post",
            "status": "Full Time",
            "status_detail": "FT",
            "completed": true,
            "venue": "MetLife Stadium",
            "city": "East Rutherford, New Jersey",
            "teams": [
              {
                "home_away": "home",
                "name": "Panama",
                "abbreviation": "PAN",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "England",
                "abbreviation": "ENG",
                "score": "2",
                "winner": true
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760485/england-panama"
          },
          {
            "id": "760481",
            "name": "Portugal at Colombia",
            "short_name": "POR @ COL",
            "date": "2026-06-27T23:30Z",
            "stage": "group-stage",
            "group": "FIFA World Cup, Group K",
            "status_state": "post",
            "status": "Full Time",
            "status_detail": "FT",
            "completed": true,
            "venue": "Hard Rock Stadium",
            "city": "Miami Gardens, Florida",
            "teams": [
              {
                "home_away": "home",
                "name": "Colombia",
                "abbreviation": "COL",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Portugal",
                "abbreviation": "POR",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760481/portugal-colombia"
          },
          {
            "id": "760482",
            "name": "Uzbekistan at Congo DR",
            "short_name": "UZB @ COD",
            "date": "2026-06-27T23:30Z",
            "stage": "group-stage",
            "group": "FIFA World Cup, Group K",
            "status_state": "post",
            "status": "Full Time",
            "status_detail": "FT",
            "completed": true,
            "venue": "Mercedes-Benz Stadium",
            "city": "Atlanta, Georgia",
            "teams": [
              {
                "home_away": "home",
                "name": "Congo DR",
                "abbreviation": "COD",
                "score": "3",
                "winner": true
              },
              {
                "home_away": "away",
                "name": "Uzbekistan",
                "abbreviation": "UZB",
                "score": "1",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760482/uzbekistan-congo-dr"
          },
          {
            "id": "760484",
            "name": "Austria at Algeria",
            "short_name": "AUT @ ALG",
            "date": "2026-06-28T02:00Z",
            "stage": "group-stage",
            "group": "FIFA World Cup, Group J",
            "status_state": "post",
            "status": "Full Time",
            "status_detail": "FT",
            "completed": true,
            "venue": "GEHA Field at Arrowhead Stadium",
            "city": "Kansas City, Missouri",
            "teams": [
              {
                "home_away": "home",
                "name": "Algeria",
                "abbreviation": "ALG",
                "score": "3",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Austria",
                "abbreviation": "AUT",
                "score": "3",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760484/austria-algeria"
          },
          {
            "id": "760483",
            "name": "Argentina at Jordan",
            "short_name": "ARG @ JOR",
            "date": "2026-06-28T02:00Z",
            "stage": "group-stage",
            "group": "FIFA World Cup, Group J",
            "status_state": "post",
            "status": "Full Time",
            "status_detail": "FT",
            "completed": true,
            "venue": "AT&T Stadium",
            "city": "Arlington, Texas",
            "teams": [
              {
                "home_away": "home",
                "name": "Jordan",
                "abbreviation": "JOR",
                "score": "1",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Argentina",
                "abbreviation": "ARG",
                "score": "3",
                "winner": true
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760483/argentina-jordan"
          },
          {
            "id": "760486",
            "name": "Canada at South Africa",
            "short_name": "CAN @ RSA",
            "date": "2026-06-28T19:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Sun, June 28th at 3:00 PM EDT",
            "completed": false,
            "venue": "SoFi Stadium",
            "city": "Inglewood, California",
            "teams": [
              {
                "home_away": "home",
                "name": "South Africa",
                "abbreviation": "RSA",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Canada",
                "abbreviation": "CAN",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760486/canada-south-africa"
          },
          {
            "id": "760487",
            "name": "Japan at Brazil",
            "short_name": "JPN @ BRA",
            "date": "2026-06-29T17:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Mon, June 29th at 1:00 PM EDT",
            "completed": false,
            "venue": "NRG Stadium",
            "city": "Houston, Texas",
            "teams": [
              {
                "home_away": "home",
                "name": "Brazil",
                "abbreviation": "BRA",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Japan",
                "abbreviation": "JPN",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760487/japan-brazil"
          },
          {
            "id": "760489",
            "name": "Paraguay at Germany",
            "short_name": "PAR @ GER",
            "date": "2026-06-29T20:30Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Mon, June 29th at 4:30 PM EDT",
            "completed": false,
            "venue": "Gillette Stadium",
            "city": "Foxborough, Massachusetts",
            "teams": [
              {
                "home_away": "home",
                "name": "Germany",
                "abbreviation": "GER",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Paraguay",
                "abbreviation": "PAR",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760489/paraguay-germany"
          },
          {
            "id": "760488",
            "name": "Morocco at Netherlands",
            "short_name": "MAR @ NED",
            "date": "2026-06-30T01:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Mon, June 29th at 9:00 PM EDT",
            "completed": false,
            "venue": "Estadio BBVA",
            "city": "Guadalupe",
            "teams": [
              {
                "home_away": "home",
                "name": "Netherlands",
                "abbreviation": "NED",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Morocco",
                "abbreviation": "MAR",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760488/morocco-netherlands"
          },
          {
            "id": "760490",
            "name": "Norway at Ivory Coast",
            "short_name": "NOR @ CIV",
            "date": "2026-06-30T17:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Tue, June 30th at 1:00 PM EDT",
            "completed": false,
            "venue": "AT&T Stadium",
            "city": "Arlington, Texas",
            "teams": [
              {
                "home_away": "home",
                "name": "Ivory Coast",
                "abbreviation": "CIV",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Norway",
                "abbreviation": "NOR",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760490/norway-ivory-coast"
          },
          {
            "id": "760492",
            "name": "Sweden at France",
            "short_name": "SWE @ FRA",
            "date": "2026-06-30T21:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Tue, June 30th at 5:00 PM EDT",
            "completed": false,
            "venue": "MetLife Stadium",
            "city": "East Rutherford, New Jersey",
            "teams": [
              {
                "home_away": "home",
                "name": "France",
                "abbreviation": "FRA",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Sweden",
                "abbreviation": "SWE",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760492/sweden-france"
          },
          {
            "id": "760491",
            "name": "Ecuador at Mexico",
            "short_name": "ECU @ MEX",
            "date": "2026-07-01T01:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Tue, June 30th at 9:00 PM EDT",
            "completed": false,
            "venue": "Estadio Banorte",
            "city": "Mexico City",
            "teams": [
              {
                "home_away": "home",
                "name": "Mexico",
                "abbreviation": "MEX",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Ecuador",
                "abbreviation": "ECU",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760491/ecuador-mexico"
          },
          {
            "id": "760495",
            "name": "Congo DR at England",
            "short_name": "COD @ ENG",
            "date": "2026-07-01T16:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Wed, July 1st at 12:00 PM EDT",
            "completed": false,
            "venue": "Mercedes-Benz Stadium",
            "city": "Atlanta, Georgia",
            "teams": [
              {
                "home_away": "home",
                "name": "England",
                "abbreviation": "ENG",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Congo DR",
                "abbreviation": "COD",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760495/congo-dr-england"
          },
          {
            "id": "760493",
            "name": "Senegal at Belgium",
            "short_name": "SEN @ BEL",
            "date": "2026-07-01T20:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Wed, July 1st at 4:00 PM EDT",
            "completed": false,
            "venue": "Lumen Field",
            "city": "Seattle, Washington",
            "teams": [
              {
                "home_away": "home",
                "name": "Belgium",
                "abbreviation": "BEL",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Senegal",
                "abbreviation": "SEN",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760493/senegal-belgium"
          },
          {
            "id": "760494",
            "name": "Bosnia-Herzegovina at United States",
            "short_name": "BIH @ USA",
            "date": "2026-07-02T00:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Wed, July 1st at 8:00 PM EDT",
            "completed": false,
            "venue": "Levi's Stadium",
            "city": "Santa Clara, California",
            "teams": [
              {
                "home_away": "home",
                "name": "United States",
                "abbreviation": "USA",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Bosnia-Herzegovina",
                "abbreviation": "BIH",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760494/bosnia-herzegovina-united-states"
          },
          {
            "id": "760497",
            "name": "Austria at Spain",
            "short_name": "AUT @ ESP",
            "date": "2026-07-02T19:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Thu, July 2nd at 3:00 PM EDT",
            "completed": false,
            "venue": "SoFi Stadium",
            "city": "Inglewood, California",
            "teams": [
              {
                "home_away": "home",
                "name": "Spain",
                "abbreviation": "ESP",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Austria",
                "abbreviation": "AUT",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760497/austria-spain"
          },
          {
            "id": "760496",
            "name": "Croatia at Portugal",
            "short_name": "CRO @ POR",
            "date": "2026-07-02T23:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Thu, July 2nd at 7:00 PM EDT",
            "completed": false,
            "venue": "BMO Field",
            "city": "Toronto",
            "teams": [
              {
                "home_away": "home",
                "name": "Portugal",
                "abbreviation": "POR",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Croatia",
                "abbreviation": "CRO",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760496/croatia-portugal"
          },
          {
            "id": "760498",
            "name": "Algeria at Switzerland",
            "short_name": "ALG @ SUI",
            "date": "2026-07-03T03:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Thu, July 2nd at 11:00 PM EDT",
            "completed": false,
            "venue": "BC Place",
            "city": "Vancouver",
            "teams": [
              {
                "home_away": "home",
                "name": "Switzerland",
                "abbreviation": "SUI",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Algeria",
                "abbreviation": "ALG",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760498/algeria-switzerland"
          },
          {
            "id": "760499",
            "name": "Egypt at Australia",
            "short_name": "EGY @ AUS",
            "date": "2026-07-03T18:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Fri, July 3rd at 2:00 PM EDT",
            "completed": false,
            "venue": "AT&T Stadium",
            "city": "Arlington, Texas",
            "teams": [
              {
                "home_away": "home",
                "name": "Australia",
                "abbreviation": "AUS",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Egypt",
                "abbreviation": "EGY",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760499/egypt-australia"
          },
          {
            "id": "760500",
            "name": "Cape Verde at Argentina",
            "short_name": "CPV @ ARG",
            "date": "2026-07-03T22:00Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Fri, July 3rd at 6:00 PM EDT",
            "completed": false,
            "venue": "Hard Rock Stadium",
            "city": "Miami Gardens, Florida",
            "teams": [
              {
                "home_away": "home",
                "name": "Argentina",
                "abbreviation": "ARG",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Cape Verde",
                "abbreviation": "CPV",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760500/cape-verde-argentina"
          },
          {
            "id": "760501",
            "name": "Ghana at Colombia",
            "short_name": "GHA @ COL",
            "date": "2026-07-04T01:30Z",
            "stage": "round-of-32",
            "group": "FIFA World Cup, Round of 32",
            "status_state": "pre",
            "status": "Scheduled",
            "status_detail": "Fri, July 3rd at 9:30 PM EDT",
            "completed": false,
            "venue": "GEHA Field at Arrowhead Stadium",
            "city": "Kansas City, Missouri",
            "teams": [
              {
                "home_away": "home",
                "name": "Colombia",
                "abbreviation": "COL",
                "score": "0",
                "winner": false
              },
              {
                "home_away": "away",
                "name": "Ghana",
                "abbreviation": "GHA",
                "score": "0",
                "winner": false
              }
            ],
            "link": "https://www.espn.com/soccer/match/_/gameId/760501/ghana-colombia"
          }
        ],
        "aggregate": {
          "total_matches": 22,
          "completed_matches": 6,
          "live_matches": 0,
          "scheduled_matches": 16,
          "latest_results": [
            "Croatia 2 - 1 Ghana",
            "Panama 0 - 2 England",
            "Colombia 0 - 0 Portugal",
            "Congo DR 3 - 1 Uzbekistan",
            "Algeria 3 - 3 Austria",
            "Jordan 1 - 3 Argentina"
          ],
          "live_now": [],
          "next_matches": [
            {
              "date": "2026-06-28T19:00Z",
              "match": "Canada at South Africa",
              "status": "Sun, June 28th at 3:00 PM EDT",
              "venue": "SoFi Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-29T17:00Z",
              "match": "Japan at Brazil",
              "status": "Mon, June 29th at 1:00 PM EDT",
              "venue": "NRG Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-29T20:30Z",
              "match": "Paraguay at Germany",
              "status": "Mon, June 29th at 4:30 PM EDT",
              "venue": "Gillette Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-30T01:00Z",
              "match": "Morocco at Netherlands",
              "status": "Mon, June 29th at 9:00 PM EDT",
              "venue": "Estadio BBVA",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-30T17:00Z",
              "match": "Norway at Ivory Coast",
              "status": "Tue, June 30th at 1:00 PM EDT",
              "venue": "AT&T Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-30T21:00Z",
              "match": "Sweden at France",
              "status": "Tue, June 30th at 5:00 PM EDT",
              "venue": "MetLife Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-01T01:00Z",
              "match": "Ecuador at Mexico",
              "status": "Tue, June 30th at 9:00 PM EDT",
              "venue": "Estadio Banorte",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-01T16:00Z",
              "match": "Congo DR at England",
              "status": "Wed, July 1st at 12:00 PM EDT",
              "venue": "Mercedes-Benz Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-01T20:00Z",
              "match": "Senegal at Belgium",
              "status": "Wed, July 1st at 4:00 PM EDT",
              "venue": "Lumen Field",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-02T00:00Z",
              "match": "Bosnia-Herzegovina at United States",
              "status": "Wed, July 1st at 8:00 PM EDT",
              "venue": "Levi's Stadium",
              "group": "FIFA World Cup, Round of 32"
            }
          ]
        }
      },
      "summary": {
        "pipeline_id": "20260628T084556Z-95b0ef82",
        "source_pipeline_id": "20260628T084556Z-95b0ef82",
        "step": "summarize_worldcup_matches",
        "style": "short_ru",
        "generated_at": "2026-06-28T08:45:56+00:00",
        "summary_text": "Found 22 World Cup matches. Completed: 6. Live: 0. Scheduled: 16. Latest results: Croatia 2 - 1 Ghana; Panama 0 - 2 England; Colombia 0 - 0 Portugal; Congo DR 3 - 1 Uzbekistan. Next matches: Canada at South Africa (Sun, June 28th at 3:00 PM EDT); Japan at Brazil (Mon, June 29th at 1:00 PM EDT); Paraguay at Germany (Mon, June 29th at 4:30 PM EDT); Morocco at Netherlands (Mon, June 29th at 9:00 PM EDT).",
        "aggregate": {
          "total_matches": 22,
          "completed_matches": 6,
          "live_matches": 0,
          "scheduled_matches": 16,
          "latest_results": [
            "Croatia 2 - 1 Ghana",
            "Panama 0 - 2 England",
            "Colombia 0 - 0 Portugal",
            "Congo DR 3 - 1 Uzbekistan",
            "Algeria 3 - 3 Austria",
            "Jordan 1 - 3 Argentina"
          ],
          "live_now": [],
          "next_matches": [
            {
              "date": "2026-06-28T19:00Z",
              "match": "Canada at South Africa",
              "status": "Sun, June 28th at 3:00 PM EDT",
              "venue": "SoFi Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-29T17:00Z",
              "match": "Japan at Brazil",
              "status": "Mon, June 29th at 1:00 PM EDT",
              "venue": "NRG Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-29T20:30Z",
              "match": "Paraguay at Germany",
              "status": "Mon, June 29th at 4:30 PM EDT",
              "venue": "Gillette Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-30T01:00Z",
              "match": "Morocco at Netherlands",
              "status": "Mon, June 29th at 9:00 PM EDT",
              "venue": "Estadio BBVA",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-30T17:00Z",
              "match": "Norway at Ivory Coast",
              "status": "Tue, June 30th at 1:00 PM EDT",
              "venue": "AT&T Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-06-30T21:00Z",
              "match": "Sweden at France",
              "status": "Tue, June 30th at 5:00 PM EDT",
              "venue": "MetLife Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-01T01:00Z",
              "match": "Ecuador at Mexico",
              "status": "Tue, June 30th at 9:00 PM EDT",
              "venue": "Estadio Banorte",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-01T16:00Z",
              "match": "Congo DR at England",
              "status": "Wed, July 1st at 12:00 PM EDT",
              "venue": "Mercedes-Benz Stadium",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-01T20:00Z",
              "match": "Senegal at Belgium",
              "status": "Wed, July 1st at 4:00 PM EDT",
              "venue": "Lumen Field",
              "group": "FIFA World Cup, Round of 32"
            },
            {
              "date": "2026-07-02T00:00Z",
              "match": "Bosnia-Herzegovina at United States",
              "status": "Wed, July 1st at 8:00 PM EDT",
              "venue": "Levi's Stadium",
              "group": "FIFA World Cup, Round of 32"
            }
          ]
        },
        "key_results": [
          "Croatia 2 - 1 Ghana",
          "Panama 0 - 2 England",
          "Colombia 0 - 0 Portugal",
          "Congo DR 3 - 1 Uzbekistan",
          "Algeria 3 - 3 Austria",
          "Jordan 1 - 3 Argentina"
        ],
        "live_now": [],
        "upcoming": [
          {
            "date": "2026-06-28T19:00Z",
            "match": "Canada at South Africa",
            "status": "Sun, June 28th at 3:00 PM EDT",
            "venue": "SoFi Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-29T17:00Z",
            "match": "Japan at Brazil",
            "status": "Mon, June 29th at 1:00 PM EDT",
            "venue": "NRG Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-29T20:30Z",
            "match": "Paraguay at Germany",
            "status": "Mon, June 29th at 4:30 PM EDT",
            "venue": "Gillette Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-30T01:00Z",
            "match": "Morocco at Netherlands",
            "status": "Mon, June 29th at 9:00 PM EDT",
            "venue": "Estadio BBVA",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-30T17:00Z",
            "match": "Norway at Ivory Coast",
            "status": "Tue, June 30th at 1:00 PM EDT",
            "venue": "AT&T Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-06-30T21:00Z",
            "match": "Sweden at France",
            "status": "Tue, June 30th at 5:00 PM EDT",
            "venue": "MetLife Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-07-01T01:00Z",
            "match": "Ecuador at Mexico",
            "status": "Tue, June 30th at 9:00 PM EDT",
            "venue": "Estadio Banorte",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-07-01T16:00Z",
            "match": "Congo DR at England",
            "status": "Wed, July 1st at 12:00 PM EDT",
            "venue": "Mercedes-Benz Stadium",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-07-01T20:00Z",
            "match": "Senegal at Belgium",
            "status": "Wed, July 1st at 4:00 PM EDT",
            "venue": "Lumen Field",
            "group": "FIFA World Cup, Round of 32"
          },
          {
            "date": "2026-07-02T00:00Z",
            "match": "Bosnia-Herzegovina at United States",
            "status": "Wed, July 1st at 8:00 PM EDT",
            "venue": "Levi's Stadium",
            "group": "FIFA World Cup, Round of 32"
          }
        ]
      },
      "saved": {
        "pipeline_id": "20260628T084556Z-95b0ef82",
        "step": "save_worldcup_summary",
        "saved_at": "2026-06-28T08:45:56+00:00",
        "saved_json": "/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260628T084556Z-95b0ef82.json",
        "saved_markdown": "/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260628T084556Z-95b0ef82.md",
        "summary_bytes": 5692,
        "markdown_bytes": 1562
      }
    },
    "error": ""
  }
]
```

## DeepSeek Answer

```json
{
  "answer": "Отлично, вот краткое объяснение выполненного пайплайна.\n\n**Использованные MCP-серверы (3):**\n1.  **`github`** — для получения сводки по репозиторию Python.\n2.  **`worldcup_scheduler`** — для получения агрегированной сводки матчей ЧМ-2026 от фонового планировщика.\n3.  **`worldcup_pipeline`** — для запуска полного цикла обработки данных ЧМ (поиск, суммаризация, сохранение).\n\n**Порядок выполнения:**\n1.  **GitHub** (первый) — получение метаданных репозитория `python/cpython` (звёзды, форки, описание). Это независимый запрос, не связанный с футболом.\n2.  **WorldCup Scheduler** (второй) — принудительное обновление и получение сводки от планировщика (10 матчей, 6 завершённых). Это быстрый срез данных.\n3.  **WorldCup Pipeline** (третий) — запуск полного пайплайна, который внутри себя выполнил три шага: поиск матчей (22 матча), создание краткой сводки (`short_ru`) и сохранение результатов в JSON и Markdown.\n\n**Что вернул каждый tool:**\n*   **`get_github_repo_summary`**: JSON с данными репозитория (название, описание, 73.5k звёзд, 34.8k форков, лицензия).\n*   **`get_worldcup_match_summary`**: JSON с агрегированной сводкой от планировщика (10 матчей, список последних результатов и ближайших игр).\n*   **`run_worldcup_pipeline`**: JSON с результатами всех трёх внутренних шагов: поиск (22 матча), суммаризация (текст сводки на русском), сохранение (пути к файлам).\n\n**Где сохранён результат:**\nФинальные файлы сохранены на сервере `worldcup_pipeline` по путям:\n*   **JSON**: `/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260628T084556Z-95b0ef82.json`\n*   **Markdown**: `/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260628T084556Z-95b0ef82.md`\n\nВсе три шага выполнены успешно.",
  "prompt_tokens": 15295,
  "completion_tokens": 600,
  "total_tokens": 15895,
  "elapsed_seconds": 5.991216100053862,
  "attempts": 1,
  "error": ""
}
```

## Check Commands

```powershell
.\.venv\Scripts\python.exe day20_mcp_orchestrator.py servers
.\.venv\Scripts\python.exe day20_mcp_orchestrator.py tools
.\.venv\Scripts\python.exe day20_mcp_orchestrator.py route "Сделай отчет по python/cpython и текущим матчам ЧМ, сохрани футбольную сводку"
.\.venv\Scripts\python.exe day20_mcp_orchestrator.py flow --repo python/cpython --force-refresh
.\.venv\Scripts\python.exe day20_mcp_orchestrator.py ask "Собери длинный отчет: GitHub repo python/cpython, текущая сводка ЧМ, и сохрани футбольный pipeline"
```