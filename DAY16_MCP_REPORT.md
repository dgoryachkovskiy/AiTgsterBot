# Day 16. MCP Connection + Tools List

## Summary

- MCP client connects to a remote MCP server.
- Transport: Streamable HTTP.
- Operation: `initialize` + `tools/list`.
- No DeepSeek API call.
- No MCP tool calls.

## Connection

- server_url: `https://mcp.deepwiki.com/mcp`
- server: DeepWiki MCP
- auth: no-auth public server
- connected: `True`
- elapsed_seconds: `2.35`
- generated_at: `2026-06-24T10:27:25+00:00`

## Tools

- tools_count: `3`
- expected_tools_ok: `True`
- missing_expected_tools: `none`

```json
[
  {
    "name": "read_wiki_structure",
    "description": "Get a list of documentation topics for a GitHub repository.\n\nArgs:\n    repoName: GitHub repository in owner/repo format (e.g. \"facebook/react\")",
    "input_schema": {
      "properties": {
        "repoName": {
          "type": "string"
        }
      },
      "required": [
        "repoName"
      ],
      "type": "object"
    }
  },
  {
    "name": "read_wiki_contents",
    "description": "View documentation about a GitHub repository.\n\nArgs:\n    repoName: GitHub repository in owner/repo format (e.g. \"facebook/react\")",
    "input_schema": {
      "properties": {
        "repoName": {
          "type": "string"
        }
      },
      "required": [
        "repoName"
      ],
      "type": "object"
    }
  },
  {
    "name": "ask_question",
    "description": "Ask any question about a GitHub repository and get an AI-powered, context-grounded response.\n\nArgs:\n    repoName: GitHub repository or list of repositories (max 10) in owner/repo format\n    question: The question to ask about the repository",
    "input_schema": {
      "properties": {
        "repoName": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          ]
        },
        "question": {
          "type": "string"
        }
      },
      "required": [
        "repoName",
        "question"
      ],
      "type": "object"
    }
  }
]
```

## How To Check

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m py_compile day16_mcp_tools_list.py
.\.venv\Scripts\python.exe day16_mcp_tools_list.py list
.\.venv\Scripts\python.exe day16_mcp_tools_list.py demo
```

## Result

- MCP connection established.
- MCP tools list returned.
- DeepWiki expected tools are present.
- Minimal Day16 requirement is complete.