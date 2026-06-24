# Day 17. Remote MCP Server + Agent Tool Call

## Summary

- Custom MCP server is deployed remotely on the VPS.
- Transport: Streamable HTTP.
- External API: GitHub public REST API.
- MCP tool: `get_github_repo_summary`.
- Agent calls the MCP tool and sends the result to DeepSeek.

## Remote MCP Connection

- url: `http://138.16.168.37:8000/mcp`
- connected: `True`
- generated_at: `2026-06-24T11:05:37+00:00`
- tools_count: `1`
- expected_tool_present: `True`

## Tool Schema

```json
[
  {
    "name": "get_github_repo_summary",
    "description": "Return a read-only summary for a public GitHub repository using the GitHub REST API. Use this when the agent needs current repository metadata.",
    "input_schema": {
      "properties": {
        "owner": {
          "description": "GitHub repository owner or organization, for example 'modelcontextprotocol'.",
          "maxLength": 100,
          "minLength": 1,
          "pattern": "^[A-Za-z0-9_.-]+$",
          "title": "Owner",
          "type": "string"
        },
        "repo": {
          "description": "GitHub repository name, for example 'python-sdk'.",
          "maxLength": 100,
          "minLength": 1,
          "pattern": "^[A-Za-z0-9_.-]+$",
          "title": "Repo",
          "type": "string"
        }
      },
      "required": [
        "owner",
        "repo"
      ],
      "title": "get_github_repo_summaryArguments",
      "type": "object"
    }
  }
]
```

## Tool Call Result

```json
{
  "source": "github_public_api",
  "owner": "modelcontextprotocol",
  "repo": "python-sdk",
  "full_name": "modelcontextprotocol/python-sdk",
  "name": "python-sdk",
  "description": "The official Python SDK for Model Context Protocol servers and clients",
  "stars": 23432,
  "forks": 3572,
  "open_issues": 568,
  "language": "Python",
  "license": "MIT",
  "updated_at": "2026-06-24T11:00:21Z",
  "html_url": "https://github.com/modelcontextprotocol/python-sdk"
}
```

## Agent Result

- DeepSeek error: `none`
- tokens: prompt=258, completion=106, total=364

```text
**Вывод по GitHub-репозиторию `modelcontextprotocol/python-sdk` (данные из MCP tool):**

Это официальный Python SDK для Model Context Protocol. Репозиторий очень популярен: **23 432 звезды** и **3 572 форка**. Написан на Python, лицензия MIT. Есть **568 открытых issues**, что при таком масштабе проекта нормально. Последнее обновление — 24 июня 2026 года.
```

## How To Check

```powershell
.\.venv\Scripts\python.exe day17_mcp_agent.py tools --url http://138.16.168.37:8000/mcp
.\.venv\Scripts\python.exe day17_mcp_agent.py call --repo modelcontextprotocol/python-sdk --url http://138.16.168.37:8000/mcp
.\.venv\Scripts\python.exe day17_mcp_agent.py demo --repo modelcontextprotocol/python-sdk --url http://138.16.168.37:8000/mcp
```