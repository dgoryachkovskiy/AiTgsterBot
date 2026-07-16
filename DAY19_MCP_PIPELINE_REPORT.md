# Day 19. MCP Tool Composition Pipeline

## What Was Built

- Remote MCP server exposes separate search, summarize, and save tools.
- Agent can call the tools as a manual chain or call the orchestrator pipeline tool.
- Data source is real ESPN FIFA World Cup scoreboard data.

## Remote MCP

- url: `http://138.16.168.37:8004/mcp`
- connected: `True`
- generated_at: `2026-06-25T19:12:13+00:00`
- tools_count: `4`

## Tools

```json
[
  {
    "name": "search_worldcup_matches",
    "description": "Fetch and search real FIFA World Cup matches from the ESPN scoreboard API.",
    "input_schema": {
      "properties": {
        "date_from": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional start date in YYYY-MM-DD format. Defaults to yesterday UTC.",
          "title": "Date From"
        },
        "date_to": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional end date in YYYY-MM-DD format. Defaults to date_from plus the server window.",
          "title": "Date To"
        },
        "query": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional text filter for team, venue, group, or match name.",
          "title": "Query"
        },
        "force_refresh": {
          "default": false,
          "description": "Kept for pipeline symmetry. Data is fetched live on every search call.",
          "title": "Force Refresh",
          "type": "boolean"
        },
        "limit": {
          "default": 50,
          "description": "Maximum number of matches to return.",
          "maximum": 100,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        }
      },
      "title": "search_worldcup_matchesArguments",
      "type": "object"
    }
  },
  {
    "name": "summarize_worldcup_matches",
    "description": "Summarize World Cup matches from a previous search pipeline_id or an explicit matches array.",
    "input_schema": {
      "properties": {
        "pipeline_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Pipeline id returned by search_worldcup_matches.",
          "title": "Pipeline Id"
        },
        "matches": {
          "anyOf": [
            {
              "items": {
                "additionalProperties": true,
                "type": "object"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional explicit matches array. If omitted, pipeline_id is used.",
          "title": "Matches"
        },
        "style": {
          "default": "short_ru",
          "description": "Summary style label, for example short_ru or report.",
          "title": "Style",
          "type": "string"
        }
      },
      "title": "summarize_worldcup_matchesArguments",
      "type": "object"
    }
  },
  {
    "name": "save_worldcup_summary",
    "description": "Save a World Cup summary from a previous pipeline step to JSON and Markdown files.",
    "input_schema": {
      "properties": {
        "pipeline_id": {
          "description": "Pipeline id returned by search_worldcup_matches.",
          "title": "Pipeline Id",
          "type": "string"
        },
        "summary": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional summary payload. If omitted, the stored summary step is used.",
          "title": "Summary"
        },
        "filename_prefix": {
          "default": "worldcup_pipeline",
          "description": "Safe filename prefix for saved JSON and Markdown files.",
          "title": "Filename Prefix",
          "type": "string"
        }
      },
      "required": [
        "pipeline_id"
      ],
      "title": "save_worldcup_summaryArguments",
      "type": "object"
    }
  },
  {
    "name": "run_worldcup_pipeline",
    "description": "Automatically run search_worldcup_matches -> summarize_worldcup_matches -> save_worldcup_summary.",
    "input_schema": {
      "properties": {
        "date_from": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional start date in YYYY-MM-DD format. Defaults to yesterday UTC.",
          "title": "Date From"
        },
        "date_to": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional end date in YYYY-MM-DD format. Defaults to date_from plus the server window.",
          "title": "Date To"
        },
        "query": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "Optional text filter for team, venue, group, or match name.",
          "title": "Query"
        },
        "force_refresh": {
          "default": false,
          "description": "Kept for pipeline symmetry. Data is fetched live on every search call.",
          "title": "Force Refresh",
          "type": "boolean"
        },
        "limit": {
          "default": 50,
          "description": "Maximum number of matches to return.",
          "maximum": 100,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        },
        "style": {
          "default": "short_ru",
          "description": "Summary style label, for example short_ru or report.",
          "title": "Style",
          "type": "string"
        },
        "filename_prefix": {
          "default": "worldcup_pipeline",
          "description": "Safe filename prefix for saved JSON and Markdown files.",
          "title": "Filename Prefix",
          "type": "string"
        }
      },
      "title": "run_worldcup_pipelineArguments",
      "type": "object"
    }
  }
]
```

## Pipeline Result

```json
{
  "mode": "automatic_pipeline_tool",
  "tool": "run_worldcup_pipeline",
  "pipeline_id": "20260625T191214Z-27797bcb",
  "automatic_chain": [
    "search_worldcup_matches",
    "summarize_worldcup_matches",
    "save_worldcup_summary"
  ],
  "transfer_ok": true,
  "aggregate": {
    "total_matches": 31,
    "completed_matches": 6,
    "live_matches": 0,
    "scheduled_matches": 25,
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
      },
      {
        "date": "2026-06-27T00:00Z",
        "match": "Saudi Arabia at Cape Verde",
        "status": "Fri, June 26th at 8:00 PM EDT",
        "venue": "NRG Stadium",
        "group": "FIFA World Cup, Group H"
      },
      {
        "date": "2026-06-27T00:00Z",
        "match": "Spain at Uruguay",
        "status": "Fri, June 26th at 8:00 PM EDT",
        "venue": "Estadio Akron",
        "group": "FIFA World Cup, Group H"
      }
    ]
  },
  "summary_text": "Found 31 World Cup matches. Completed: 6. Live: 0. Scheduled: 25. Latest results: Bosnia-Herzegovina 3 - 1 Qatar; Switzerland 2 - 1 Canada; Morocco 4 - 2 Haiti; Scotland 0 - 3 Brazil. Next matches: Ivory Coast at Curaçao (Thu, June 25th at 4:00 PM EDT); Germany at Ecuador (Thu, June 25th at 4:00 PM EDT); Sweden at Japan (Thu, June 25th at 7:00 PM EDT); Netherlands at Tunisia (Thu, June 25th at 7:00 PM EDT).",
  "saved": {
    "pipeline_id": "20260625T191214Z-27797bcb",
    "step": "save_worldcup_summary",
    "saved_at": "2026-06-25T19:12:14+00:00",
    "saved_json": "/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260625T191214Z-27797bcb.json",
    "saved_markdown": "/opt/day19-worldcup-pipeline-mcp/data/summaries/worldcup_pipeline_20260625T191214Z-27797bcb.md",
    "summary_bytes": 5675,
    "markdown_bytes": 1580
  },
  "elapsed_seconds": 1.9
}
```

## DeepSeek Agent Answer

- used: `False`
- error: `none`
- tokens: prompt=0, completion=0, total=0

```text

```

## Check Commands

```powershell
.\.venv\Scripts\python.exe day19_pipeline_agent.py tools --url http://138.16.168.37:8004/mcp
.\.venv\Scripts\python.exe day19_pipeline_agent.py chain --force-refresh --url http://138.16.168.37:8004/mcp
.\.venv\Scripts\python.exe day19_pipeline_agent.py pipeline --force-refresh --url http://138.16.168.37:8004/mcp
```