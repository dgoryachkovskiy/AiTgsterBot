import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8011


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def project_root_from_arg(value: str | None) -> Path:
    return Path(value or ".").resolve()


def run_git(root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git command failed: {' '.join(args)}")
    return completed.stdout.strip()


def get_branch(root: Path) -> dict[str, Any]:
    branch = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit = run_git(root, ["rev-parse", "--short", "HEAD"])
    return {
        "project_root": str(root),
        "branch": branch,
        "commit": commit,
    }


def get_file_list(root: Path, limit: int) -> dict[str, Any]:
    tracked = run_git(root, ["ls-files"]).splitlines()
    untracked = run_git(root, ["ls-files", "--others", "--exclude-standard"]).splitlines()
    files = tracked + [item for item in untracked if item not in set(tracked)]
    return {
        "project_root": str(root),
        "files_total": len(files),
        "files_returned": min(limit, len(files)),
        "files": files[:limit],
    }


def get_diff(root: Path) -> dict[str, Any]:
    shortstat = run_git(root, ["diff", "--shortstat"]) or "clean tracked diff"
    name_status = run_git(root, ["diff", "--name-status"]).splitlines()
    return {
        "project_root": str(root),
        "shortstat": shortstat,
        "changed_files": name_status,
    }


def create_mcp_server(root: Path, host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        name="day31-project-context-mcp",
        instructions="Read-only MCP server exposing current project git and file context.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="get_git_branch",
        description="Return current git branch and short commit for the project.",
        structured_output=True,
    )
    async def get_git_branch() -> dict[str, Any]:
        return get_branch(root)

    @mcp.tool(
        name="list_project_files",
        description="Return a read-only list of tracked and untracked project files.",
        structured_output=True,
    )
    async def list_project_files(
        limit: Annotated[int, Field(description="Maximum number of file paths to return.", ge=1, le=500)] = 120,
    ) -> dict[str, Any]:
        return get_file_list(root, limit)

    @mcp.tool(
        name="get_git_diff_stat",
        description="Return current git diff stat and changed tracked files.",
        structured_output=True,
    )
    async def get_git_diff_stat() -> dict[str, Any]:
        return get_diff(root)

    return mcp


def print_tool_schema(root: Path, host: str, port: int) -> None:
    server = create_mcp_server(root, host, port)
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
    parser = argparse.ArgumentParser(description="Day31 read-only project MCP server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument("--project-root", default=".")

    schema_parser = subparsers.add_parser("schema")
    schema_parser.add_argument("--host", default=DEFAULT_HOST)
    schema_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    schema_parser.add_argument("--project-root", default=".")

    args = parser.parse_args()
    root = project_root_from_arg(args.project_root)
    if args.command == "serve":
        server = create_mcp_server(root, args.host, args.port)
        print(f"starting day31-project-context-mcp on {args.host}:{args.port}/mcp root={root}", flush=True)
        server.run(transport="streamable-http")
    elif args.command == "schema":
        print_tool_schema(root, args.host, args.port)


if __name__ == "__main__":
    main()
