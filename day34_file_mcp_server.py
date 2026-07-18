from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8035
DEFAULT_MAX_READ_CHARS = 120000


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def read_text_file(path: Path, max_chars: int = DEFAULT_MAX_READ_CHARS) -> tuple[str, bool]:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError(f"Refusing to read binary file: {path}")
    text = None
    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    truncated = len(text) > max_chars
    return text[:max_chars], truncated


def safe_path(root: Path, raw_path: str, must_exist: bool = False) -> Path:
    if not raw_path or raw_path.strip() in {".", ""}:
        raise ValueError("path is required")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    root_resolved = root.resolve(strict=True)
    try:
        common = os.path.commonpath([str(root_resolved).lower(), str(candidate).lower()])
    except ValueError as exc:
        raise ValueError(f"Path is outside project root: {raw_path}") from exc
    if common != str(root_resolved).lower():
        raise ValueError(f"Path is outside project root: {raw_path}")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"File not found: {raw_path}")
    return candidate


def rel_path(root: Path, path: Path) -> str:
    root = root.resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path.resolve(strict=False).relative_to(root)
    return str(relative).replace("\\", "/")


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
    return completed.stdout


def git_untracked(root: Path, paths: list[str]) -> list[str]:
    args = ["ls-files", "--others", "--exclude-standard"]
    if paths:
        args.extend(["--", *paths])
    output = run_git(root, args)
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def synthetic_new_file_diff(root: Path, path: str, max_chars: int) -> str:
    target = safe_path(root, path, must_exist=True)
    if not target.is_file():
        return ""
    try:
        text, _ = read_text_file(target, max_chars)
    except (OSError, ValueError):
        return ""
    return "".join(
        difflib.unified_diff(
            [],
            text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def split_globs(globs: list[str] | None) -> list[str]:
    if not globs:
        return []
    result = []
    for item in globs:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                result.append(part.replace("\\", "/"))
    return result


def is_excluded(path: str) -> bool:
    parts = {part.lower() for part in Path(path.replace("\\", "/")).parts}
    excluded = {
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "day21_index_store",
        "day22_rag_store",
        "day23_rag_store",
        "day24_rag_store",
        "day25_chat_store",
        "day26_local_llm_store",
        "day27_local_llm_app_store",
        "day28_local_rag_store",
        "day29_optimization_store",
        "day30_private_llm_store",
        "day31_project_index_store",
        "day31_project_assistant_store",
        "day32_code_review_index_store",
        "day32_code_review_store",
        "day33_support_index_store",
        "day33_support_store",
        "day34_file_assistant_store",
    }
    return bool(parts & excluded)


def iter_project_files(root: Path) -> list[tuple[Path, str]]:
    root = root.resolve(strict=True)
    files: list[tuple[Path, str]] = []
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        try:
            relative_dir = rel_path(root, current_path)
        except ValueError:
            dirnames[:] = []
            continue
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not is_excluded(f"{relative_dir}/{dirname}" if relative_dir != "." else dirname)
        ]
        for filename in filenames:
            path = current_path / filename
            try:
                relative = rel_path(root, path)
            except ValueError:
                continue
            if is_excluded(relative):
                continue
            files.append((path, relative))
    return files


def matches_globs(path: str, globs: list[str]) -> bool:
    if not globs:
        return True
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in globs)


def rg_search(root: Path, query: str, globs: list[str], max_results: int) -> list[dict[str, Any]]:
    command = [
        "rg",
        "--line-number",
        "--column",
        "--hidden",
        "--no-heading",
        "--glob",
        "!.git/**",
        "--glob",
        "!.venv/**",
    ]
    for glob in globs:
        command.extend(["--glob", glob])
    command.append(query)
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(completed.stderr.strip() or "rg search failed")
    matches = []
    for line in completed.stdout.splitlines():
        if len(matches) >= max_results:
            break
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        path, line_no, column, text = parts
        matches.append(
            {
                "path": path.replace("\\", "/"),
                "line": int(line_no) if line_no.isdigit() else 0,
                "column": int(column) if column.isdigit() else 0,
                "text": text.strip(),
            }
        )
    return matches


def python_search(root: Path, query: str, globs: list[str], max_results: int) -> list[dict[str, Any]]:
    matches = []
    for path, relative in iter_project_files(root):
        if len(matches) >= max_results:
            break
        if not matches_globs(relative, globs):
            continue
        try:
            text, _ = read_text_file(path)
        except (OSError, ValueError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                matches.append(
                    {
                        "path": relative,
                        "line": line_no,
                        "column": max(1, line.lower().find(query.lower()) + 1),
                        "text": line.strip(),
                    }
                )
                if len(matches) >= max_results:
                    break
    return matches


def list_files(root: Path, globs: list[str], max_results: int) -> list[str]:
    try:
        output = run_git(root, ["ls-files", "--others", "--exclude-standard", "--cached"])
        candidates = [line.strip() for line in output.splitlines() if line.strip()]
    except RuntimeError:
        candidates = [relative for _, relative in iter_project_files(root)]
    result = []
    seen = set()
    for path in candidates:
        normalized = path.replace("\\", "/")
        if normalized in seen or is_excluded(normalized) or not matches_globs(normalized, globs):
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= max_results:
            break
    return result


def create_mcp_server(root: Path, host: str, port: int) -> FastMCP:
    root = root.resolve(strict=True)
    mcp = FastMCP(
        name="day34-project-file-mcp",
        instructions="Read/write MCP server for project files. Paths are restricted to the configured project root.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="read_project_file",
        description="Read one UTF-8/cp1251 text file from the project root.",
        structured_output=True,
    )
    async def read_project_file(
        path: Annotated[str, Field(description="Relative file path inside project root.", min_length=1, max_length=500)],
        max_chars: Annotated[int, Field(description="Maximum characters to return.", ge=1, le=500000)] = DEFAULT_MAX_READ_CHARS,
    ) -> dict[str, Any]:
        target = safe_path(root, path, must_exist=True)
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        text, truncated = read_text_file(target, max_chars)
        return {
            "path": rel_path(root, target),
            "absolute_path": str(target),
            "content": text,
            "chars": len(text),
            "truncated": truncated,
        }

    @mcp.tool(
        name="search_project_files",
        description="Search text across project files using ripgrep with optional glob filters.",
        structured_output=True,
    )
    async def search_project_files(
        query: Annotated[str, Field(description="Search query text.", min_length=1, max_length=1000)],
        globs: Annotated[list[str], Field(description="Glob filters such as *.py or docs/*.md. Empty means all files.")] = [],
        max_results: Annotated[int, Field(description="Maximum matches to return.", ge=1, le=500)] = 200,
    ) -> dict[str, Any]:
        parsed_globs = split_globs(globs)
        try:
            matches = rg_search(root, query, parsed_globs, max_results)
            engine = "rg"
        except (FileNotFoundError, RuntimeError):
            matches = python_search(root, query, parsed_globs, max_results)
            engine = "python"
        return {
            "query": query,
            "globs": parsed_globs,
            "engine": engine,
            "count": len(matches),
            "matches": matches,
        }

    @mcp.tool(
        name="list_project_files",
        description="List project files with optional glob filters.",
        structured_output=True,
    )
    async def list_project_files(
        globs: Annotated[list[str], Field(description="Glob filters such as *.py or docs/*.md. Empty means all files.")] = [],
        max_results: Annotated[int, Field(description="Maximum paths to return.", ge=1, le=2000)] = 300,
    ) -> dict[str, Any]:
        parsed_globs = split_globs(globs)
        files = list_files(root, parsed_globs, max_results)
        return {
            "root": str(root),
            "globs": parsed_globs,
            "count": len(files),
            "files": files,
        }

    @mcp.tool(
        name="write_project_file",
        description="Create or overwrite a text file inside project root.",
        structured_output=True,
    )
    async def write_project_file(
        path: Annotated[str, Field(description="Relative file path inside project root.", min_length=1, max_length=500)],
        content: Annotated[str, Field(description="New UTF-8 file content.", min_length=0)],
        overwrite: Annotated[bool, Field(description="Allow replacing an existing file.")] = False,
        create_dirs: Annotated[bool, Field(description="Create parent directories when missing.")] = True,
    ) -> dict[str, Any]:
        target = safe_path(root, path, must_exist=False)
        existed = target.exists()
        if existed and not overwrite:
            raise FileExistsError(f"File exists and overwrite=false: {path}")
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return {
            "path": rel_path(root, target),
            "absolute_path": str(target),
            "existed": existed,
            "chars": len(content),
            "bytes": len(content.encode("utf-8")),
        }

    @mcp.tool(
        name="get_git_diff",
        description="Return git diff for selected paths or the whole working tree.",
        structured_output=True,
    )
    async def get_git_diff(
        paths: Annotated[list[str], Field(description="Optional relative paths to include in diff.")] = [],
        max_chars: Annotated[int, Field(description="Maximum diff characters to return.", ge=1, le=500000)] = 120000,
    ) -> dict[str, Any]:
        parsed_paths = [rel_path(root, safe_path(root, path, must_exist=False)) for path in split_globs(paths)]
        diff_args = ["diff", "--", *parsed_paths] if parsed_paths else ["diff"]
        diff = run_git(root, diff_args)
        untracked = git_untracked(root, parsed_paths)
        included_untracked = []
        for path in untracked:
            synthetic_diff = synthetic_new_file_diff(root, path, max_chars)
            if not synthetic_diff:
                continue
            included_untracked.append(path)
            diff += ("\n" if diff and not diff.endswith("\n") else "")
            diff += synthetic_diff
        status = run_git(root, ["status", "--short"])
        truncated = len(diff) > max_chars
        return {
            "paths": parsed_paths,
            "untracked_included": included_untracked,
            "status_short": status.strip().splitlines(),
            "diff": diff[:max_chars],
            "diff_chars": len(diff),
            "truncated": truncated,
        }

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
    print(to_json(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description="Day34 project file MCP server.")
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
    root = Path(args.project_root).resolve()
    if args.command == "serve":
        server = create_mcp_server(root, args.host, args.port)
        print(f"starting day34-project-file-mcp on {args.host}:{args.port}/mcp root={root}", flush=True)
        server.run(transport="streamable-http")
    elif args.command == "schema":
        print_tool_schema(root, args.host, args.port)


if __name__ == "__main__":
    main()
