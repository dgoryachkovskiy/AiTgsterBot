import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8012


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def project_root_from_arg(value: str | None) -> Path:
    return Path(value or ".").resolve()


def run_git(root: Path, args: list[str], max_chars: int | None = None) -> str:
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
    output = completed.stdout
    if max_chars is not None and len(output) > max_chars:
        return output[:max_chars]
    return output


def validate_ref(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("git ref must not be empty")
    if any(char in cleaned for char in ["\n", "\r", "\0"]):
        raise ValueError("git ref contains invalid characters")
    return cleaned


def validate_repo_path(value: str) -> str:
    cleaned = value.replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or ".." in cleaned.split("/"):
        raise ValueError("path must be a relative repository path")
    if any(char in cleaned for char in ["\n", "\r", "\0"]):
        raise ValueError("path contains invalid characters")
    return cleaned


def current_branch(root: Path) -> dict[str, Any]:
    return {
        "project_root": str(root),
        "branch": run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip(),
        "commit": run_git(root, ["rev-parse", "--short", "HEAD"]).strip(),
    }


def changed_files(root: Path, base_ref: str, head_ref: str) -> dict[str, Any]:
    base = validate_ref(base_ref)
    head = validate_ref(head_ref)
    status_lines = run_git(root, ["diff", "--name-status", base, head]).splitlines()
    files = []
    for line in status_lines:
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        path = parts[-1] if len(parts) > 1 else ""
        if path:
            files.append({"status": status, "path": path})
    return {
        "base_ref": base,
        "head_ref": head,
        "files_count": len(files),
        "files": files,
    }


def pr_diff(root: Path, base_ref: str, head_ref: str, max_chars: int) -> dict[str, Any]:
    base = validate_ref(base_ref)
    head = validate_ref(head_ref)
    raw = run_git(root, ["diff", "--no-color", "--unified=80", base, head])
    truncated = len(raw) > max_chars
    return {
        "base_ref": base,
        "head_ref": head,
        "max_chars": max_chars,
        "diff_chars": len(raw),
        "truncated": truncated,
        "diff": raw[:max_chars] if truncated else raw,
    }


def file_snapshot(root: Path, path: str, ref: str, max_chars: int) -> dict[str, Any]:
    safe_path = validate_repo_path(path)
    safe_ref = validate_ref(ref)
    content = run_git(root, ["show", f"{safe_ref}:{safe_path}"], max_chars=max_chars)
    truncated = len(content) >= max_chars
    return {
        "ref": safe_ref,
        "path": safe_path,
        "max_chars": max_chars,
        "truncated": truncated,
        "content": content,
    }


def create_mcp_server(root: Path, host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        name="day32-code-review-mcp",
        instructions="Read-only MCP server for AI code review. Exposes git branch, changed files, diff, and file snapshots.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(name="get_git_branch", description="Return current git branch and short commit.", structured_output=True)
    async def get_git_branch() -> dict[str, Any]:
        return current_branch(root)

    @mcp.tool(name="get_changed_files", description="Return changed files between base and head refs.", structured_output=True)
    async def get_changed_files(
        base_ref: Annotated[str, Field(description="Base git ref, for example origin/main.")],
        head_ref: Annotated[str, Field(description="Head git ref, for example HEAD.")],
    ) -> dict[str, Any]:
        return changed_files(root, base_ref, head_ref)

    @mcp.tool(name="get_pr_diff", description="Return unified diff between base and head refs.", structured_output=True)
    async def get_pr_diff(
        base_ref: Annotated[str, Field(description="Base git ref, for example origin/main.")],
        head_ref: Annotated[str, Field(description="Head git ref, for example HEAD.")],
        max_chars: Annotated[int, Field(description="Maximum diff characters to return.", ge=1000, le=200000)] = 60000,
    ) -> dict[str, Any]:
        return pr_diff(root, base_ref, head_ref, max_chars)

    @mcp.tool(name="get_file_snapshot", description="Return file content at a git ref.", structured_output=True)
    async def get_file_snapshot(
        path: Annotated[str, Field(description="Relative repository file path.")],
        ref: Annotated[str, Field(description="Git ref to read from, for example HEAD.")],
        max_chars: Annotated[int, Field(description="Maximum file characters to return.", ge=100, le=60000)] = 20000,
    ) -> dict[str, Any]:
        return file_snapshot(root, path, ref, max_chars)

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
    parser = argparse.ArgumentParser(description="Day32 read-only MCP server for code review.")
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
        print(f"starting day32-code-review-mcp on {args.host}:{args.port}/mcp root={root}", flush=True)
        server.run(transport="streamable-http")
    elif args.command == "schema":
        print_tool_schema(root, args.host, args.port)


if __name__ == "__main__":
    main()
