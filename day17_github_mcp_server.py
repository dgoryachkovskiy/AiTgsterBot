import argparse
import json
import re
import sys
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
GITHUB_API_BASE = "https://api.github.com"
REPO_PART_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def validate_repo_part(label: str, value: str) -> str:
    cleaned = value.strip()
    if not REPO_PART_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{label} must contain only letters, numbers, dots, underscores, or hyphens")
    return cleaned


async def fetch_github_repo_summary(owner: str, repo: str) -> dict[str, Any]:
    owner = validate_repo_part("owner", owner)
    repo = validate_repo_part("repo", repo)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "day17-remote-mcp-server",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url, headers=headers)
    if response.status_code == 404:
        raise ValueError(f"GitHub repository not found: {owner}/{repo}")
    response.raise_for_status()
    data = response.json()
    license_data = data.get("license") or {}
    return {
        "source": "github_public_api",
        "owner": owner,
        "repo": repo,
        "full_name": data.get("full_name"),
        "name": data.get("name"),
        "description": data.get("description"),
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "open_issues": data.get("open_issues_count"),
        "language": data.get("language"),
        "license": license_data.get("spdx_id") or license_data.get("name"),
        "updated_at": data.get("updated_at"),
        "html_url": data.get("html_url"),
    }


def create_mcp_server(host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        name="day17-github-mcp",
        instructions="Read-only MCP server that exposes public GitHub repository summaries.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="get_github_repo_summary",
        description=(
            "Return a read-only summary for a public GitHub repository using the GitHub REST API. "
            "Use this when the agent needs current repository metadata."
        ),
        structured_output=True,
    )
    async def get_github_repo_summary(
        owner: Annotated[
            str,
            Field(
                description="GitHub repository owner or organization, for example 'modelcontextprotocol'.",
                min_length=1,
                max_length=100,
                pattern=r"^[A-Za-z0-9_.-]+$",
            ),
        ],
        repo: Annotated[
            str,
            Field(
                description="GitHub repository name, for example 'python-sdk'.",
                min_length=1,
                max_length=100,
                pattern=r"^[A-Za-z0-9_.-]+$",
            ),
        ],
    ) -> dict[str, Any]:
        return await fetch_github_repo_summary(owner, repo)

    return mcp


def print_tool_schema(host: str, port: int) -> None:
    server = create_mcp_server(host, port)
    tools = server._tool_manager.list_tools()
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
            "output_schema": tool.output_schema,
        }
        for tool in tools
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 17 deployed GitHub MCP server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run Streamable HTTP MCP server")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    schema_parser = subparsers.add_parser("schema", help="print registered tool schema")
    schema_parser.add_argument("--host", default=DEFAULT_HOST)
    schema_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = parser.parse_args()
    if args.command == "serve":
        server = create_mcp_server(args.host, args.port)
        print(f"starting day17-github-mcp on {args.host}:{args.port}/mcp", flush=True)
        server.run(transport="streamable-http")
    elif args.command == "schema":
        print_tool_schema(args.host, args.port)


if __name__ == "__main__":
    main()
