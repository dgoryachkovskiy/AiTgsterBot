from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI


DEFAULT_STORE_DIR = "day34_file_assistant_store"
DEFAULT_REPORT_PATH = "DAY34_FILE_ASSISTANT_REPORT.md"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8035
DEFAULT_MCP_URL = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}/mcp"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 75
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
DEFAULT_SEARCH_GLOBS = ["*.py", "*.md", ".env.example", ".github/**/*.yml", ".github/**/*.yaml"]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class CommandTrace:
    command: str
    generated_at: str
    inputs: dict[str, Any]
    mcp_tools: list[str]
    files_read: list[str]
    files_written: list[str]
    result: dict[str, Any]
    tokens: dict[str, int]
    elapsed_seconds: float
    llm_used: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def store_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY34_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return {"value": value}


def extract_mcp_json(call_result: Any) -> dict[str, Any]:
    raw = model_to_dict(call_result)
    if raw.get("structuredContent"):
        return raw["structuredContent"]
    if raw.get("structured_content"):
        return raw["structured_content"]
    content = raw.get("content") or getattr(call_result, "content", []) or []
    for item in content:
        item_raw = model_to_dict(item)
        text = item_raw.get("text") or getattr(item, "text", "")
        if text:
            if text.startswith("Error executing tool"):
                return {"error": text}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return raw


async def list_mcp_tools(url: str, timeout_seconds: float) -> list[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> list[dict[str, Any]]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
                return [
                    {
                        "name": model_to_dict(tool).get("name"),
                        "description": model_to_dict(tool).get("description"),
                        "input_schema": model_to_dict(tool).get("inputSchema") or model_to_dict(tool).get("input_schema"),
                    }
                    for tool in response.tools
                ]

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


async def call_mcp_tool(url: str, tool_name: str, args: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> dict[str, Any]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, args)
                return extract_mcp_json(result)

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


def mcp_url_from_args(args: argparse.Namespace) -> str:
    return args.mcp_url or os.getenv("DAY34_MCP_URL", DEFAULT_MCP_URL)


def start_mcp_server(root: Path, store_dir: Path, host: str, port: int) -> subprocess.Popen[str]:
    store_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            sys.executable,
            "day34_file_mcp_server.py",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--project-root",
            str(root),
        ],
        cwd=root,
        text=True,
        stdout=(store_dir / "mcp_server.stdout.log").open("w", encoding="utf-8"),
        stderr=(store_dir / "mcp_server.stderr.log").open("w", encoding="utf-8"),
    )


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def wait_for_mcp(url: str, timeout_seconds: float) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return asyncio.run(list_mcp_tools(url, 5))
        except Exception as exc:
            last_error = exc
            time.sleep(0.4)
    raise RuntimeError(f"MCP server did not become ready: {last_error}")


class FileAssistantSession:
    def __init__(self, args: argparse.Namespace):
        load_dotenv()
        self.args = args
        self.root = Path(args.project_root).resolve()
        self.store_dir = store_dir_from_env(args.store_dir)
        self.url = mcp_url_from_args(args)
        self.timeout = float(parse_int(os.getenv("DAY34_MCP_TIMEOUT_SECONDS"), 20))
        self.process: subprocess.Popen[str] | None = None
        self.tools: list[dict[str, Any]] = []
        self.files_read: list[str] = []
        self.files_written: list[str] = []

    def __enter__(self) -> "FileAssistantSession":
        if not self.args.mcp_url and not self.args.no_spawn_mcp:
            self.process = start_mcp_server(self.root, self.store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
        self.tools = wait_for_mcp(self.url, 15)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        stop_process(self.process)

    @property
    def tool_names(self) -> list[str]:
        return [str(tool.get("name")) for tool in self.tools]

    def call(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = asyncio.run(call_mcp_tool(self.url, tool_name, payload, self.timeout))
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result

    def read_file(self, path: str, max_chars: int = 120000) -> dict[str, Any]:
        result = self.call("read_project_file", {"path": path, "max_chars": max_chars})
        self.files_read.append(str(result.get("path") or path))
        return result

    def search(self, query: str, globs: list[str] | None = None, max_results: int = 80) -> dict[str, Any]:
        return self.call("search_project_files", {"query": query, "globs": globs or DEFAULT_SEARCH_GLOBS, "max_results": max_results})

    def list_files(self, globs: list[str] | None = None, max_results: int = 300) -> dict[str, Any]:
        return self.call("list_project_files", {"globs": globs or [], "max_results": max_results})

    def write_file(self, path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
        result = self.call("write_project_file", {"path": path, "content": content, "overwrite": overwrite, "create_dirs": True})
        self.files_written.append(str(result.get("path") or path))
        return result

    def git_diff(self, paths: list[str] | None = None, max_chars: int = 120000) -> dict[str, Any]:
        return self.call("get_git_diff", {"paths": paths or [], "max_chars": max_chars})


def deepseek_client() -> tuple[OpenAI, str]:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    model = os.getenv("DAY34_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_int(os.getenv("DAY34_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model


def call_deepseek(messages: list[dict[str, str]], max_tokens: int = 1800) -> tuple[str, dict[str, int], float]:
    started = time.perf_counter()
    client, model = deepseek_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
        extra_body=THINKING_DISABLED,
    )
    usage = response.usage
    tokens = {
        "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion": int(getattr(usage, "completion_tokens", 0) or 0),
        "total": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return response.choices[0].message.content or "", tokens, round(time.perf_counter() - started, 3)


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:markdown|md|text)?\s*(.*?)\s*```$", stripped, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() + "\n" if match else stripped + "\n"


def unified_diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def save_trace(store_dir: Path, name: str, trace: CommandTrace) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / f"{name}.json").write_text(to_json(asdict(trace)), encoding="utf-8")


def command_mcp_tools(args: argparse.Namespace) -> int:
    with FileAssistantSession(args) as session:
        print(f"mcp_url={session.url}")
        print("connected=True")
        print(f"tools_count={len(session.tools)}")
        for tool in session.tools:
            print(f"- {tool['name']}: {tool['description']}")
    return 0


def command_find_usages(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    with FileAssistantSession(args) as session:
        search_result = session.search(args.query, args.globs, args.max_results)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for match in search_result.get("matches", []):
            grouped.setdefault(str(match.get("path")), []).append(match)
        files_to_read = list(grouped)[: max(3, min(8, len(grouped)))]
        snapshots = [session.read_file(path, 20000) for path in files_to_read]
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a project file assistant. Analyze real search results. "
                    "Answer in Russian. Mention concrete files and lines. Do not invent files."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Goal: find and explain usages of `{args.query}`.\n\n"
                    f"Search result JSON:\n{to_json(search_result)}\n\n"
                    f"Read file snapshots:\n{to_json([{k: v for k, v in item.items() if k != 'content'} | {'content_preview': item.get('content', '')[:5000]} for item in snapshots])}\n\n"
                    "Return concise analysis with: summary, grouped usages, risk/notes, next check commands."
                ),
            },
        ]
        analysis, tokens, llm_elapsed = call_deepseek(messages, max_tokens=1600)
        trace = CommandTrace(
            command="find-usages",
            generated_at=now_iso(),
            inputs={"query": args.query, "globs": args.globs, "max_results": args.max_results},
            mcp_tools=session.tool_names,
            files_read=session.files_read,
            files_written=[],
            result={"search": search_result, "analysis": analysis},
            tokens=tokens,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            llm_used=True,
        )
        save_trace(session.store_dir, "last_find_usages", trace)
        print(f"query={args.query}")
        print(f"matches={search_result.get('count')}")
        print(f"files_read={', '.join(session.files_read) if session.files_read else '-'}")
        print(f"tokens={to_json(tokens)} elapsed_llm={llm_elapsed}")
        print("analysis:")
        print(analysis)
    return 0


def command_update_docs(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    target = args.target
    with FileAssistantSession(args) as session:
        required_files = ["bot.py", "README.md", ".env.example", target]
        docs_files = session.list_files(["docs/*.md"], 50).get("files", [])
        files_to_read = []
        for path in required_files + docs_files:
            if path and path not in files_to_read:
                files_to_read.append(path)
        snapshots = []
        target_existing = ""
        for path in files_to_read:
            try:
                item = session.read_file(path, 80000)
            except Exception as exc:
                item = {"path": path, "error": str(exc), "content": ""}
            if item.get("path") == target:
                target_existing = str(item.get("content", ""))
            snapshots.append(item)
        messages = [
            {
                "role": "system",
                "content": (
                    "You update project documentation from real source files. "
                    "Return only full Markdown content for target file. No fences. "
                    "Preserve useful existing sections, add Day34 file assistant commands, keep commands accurate. "
                    "Every command example must be directly runnable and include all required CLI arguments."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target file: {target}\n"
                    "Need update docs for real Day34 project file assistant.\n"
                    "Include these exact runnable Day34 commands:\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py mcp-tools\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py find-usages \"DEEPSEEK_API_KEY\"\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py update-docs --target docs/PROJECT_COMMANDS.md --apply\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py generate-file --kind readme --target docs/DAY34_FILE_ASSISTANT_USAGE.md --apply\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py check-rules\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py prepare-diff\n"
                    "Source snapshots:\n"
                    f"{to_json([{k: v for k, v in item.items() if k != 'content'} | {'content': str(item.get('content', ''))[:16000]} for item in snapshots])}"
                ),
            },
        ]
        new_content, tokens, llm_elapsed = call_deepseek(messages, max_tokens=2600)
        new_content = strip_markdown_fence(new_content)
        diff = unified_diff(target_existing, new_content, target)
        write_result: dict[str, Any] | None = None
        if args.apply:
            write_result = session.write_file(target, new_content, overwrite=True)
            git_diff = session.git_diff([target], 120000)
        else:
            git_diff = {"diff": diff, "dry_run": True, "truncated": False, "diff_chars": len(diff)}
        trace = CommandTrace(
            command="update-docs",
            generated_at=now_iso(),
            inputs={"target": target, "apply": args.apply},
            mcp_tools=session.tool_names,
            files_read=session.files_read,
            files_written=session.files_written,
            result={"write_result": write_result, "diff": git_diff},
            tokens=tokens,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            llm_used=True,
        )
        save_trace(session.store_dir, "last_update_docs", trace)
        print(f"target={target}")
        print(f"apply={args.apply}")
        print(f"files_read={', '.join(session.files_read)}")
        print(f"files_written={', '.join(session.files_written) if session.files_written else '-'}")
        print(f"tokens={to_json(tokens)} elapsed_llm={llm_elapsed}")
        print("diff:")
        print(git_diff.get("diff", ""))
    return 0


def command_generate_file(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    with FileAssistantSession(args) as session:
        context_paths = ["README.md", ".env.example", "docs/PROJECT_COMMANDS.md", "docs/PROJECT_STRUCTURE.md"]
        snapshots = []
        for path in context_paths:
            try:
                snapshots.append(session.read_file(path, 60000))
            except Exception as exc:
                snapshots.append({"path": path, "error": str(exc), "content": ""})
        kind_instructions = {
            "readme": "Create a practical usage guide for Day34 file assistant.",
            "adr": "Create an Architecture Decision Record for using MCP file tools for project operations.",
            "changelog": "Create a changelog entry summarizing current project file-assistant changes.",
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You generate real project markdown files from source context. "
                    "Return only Markdown content. No fences. Keep it concise and command-focused. "
                    "Every command example must be directly runnable and include all required CLI arguments."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Kind: {args.kind}\nTarget: {args.target}\n"
                    f"Task: {kind_instructions[args.kind]}\n"
                    "Must include these exact runnable commands and verification steps for this repo:\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py mcp-tools\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py find-usages \"DEEPSEEK_API_KEY\"\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py update-docs --target docs/PROJECT_COMMANDS.md --apply\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py generate-file --kind readme --target docs/DAY34_FILE_ASSISTANT_USAGE.md --apply\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py check-rules\n"
                    ".\\.venv\\Scripts\\python.exe day34_file_assistant.py prepare-diff\n"
                    f"Context:\n{to_json([{k: v for k, v in item.items() if k != 'content'} | {'content': str(item.get('content', ''))[:12000]} for item in snapshots])}"
                ),
            },
        ]
        content, tokens, llm_elapsed = call_deepseek(messages, max_tokens=2200)
        content = strip_markdown_fence(content)
        write_result = None
        if args.apply:
            write_result = session.write_file(args.target, content, overwrite=True)
            diff = session.git_diff([args.target], 120000)
        else:
            diff = {"diff": unified_diff("", content, args.target), "dry_run": True, "truncated": False, "diff_chars": len(content)}
        trace = CommandTrace(
            command="generate-file",
            generated_at=now_iso(),
            inputs={"kind": args.kind, "target": args.target, "apply": args.apply},
            mcp_tools=session.tool_names,
            files_read=session.files_read,
            files_written=session.files_written,
            result={"write_result": write_result, "diff": diff},
            tokens=tokens,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            llm_used=True,
        )
        save_trace(session.store_dir, "last_generate_file", trace)
        print(f"kind={args.kind}")
        print(f"target={args.target}")
        print(f"apply={args.apply}")
        print(f"files_read={', '.join(session.files_read)}")
        print(f"files_written={', '.join(session.files_written) if session.files_written else '-'}")
        print(f"tokens={to_json(tokens)} elapsed_llm={llm_elapsed}")
        print("diff:")
        print(diff.get("diff", ""))
    return 0


def extract_env_vars(text: str) -> set[str]:
    pattern = r"os\.getenv\([\"']([A-Z0-9_]+)[\"']|require_env\([\"']([A-Z0-9_]+)[\"']"
    found = set()
    for match in re.finditer(pattern, text):
        found.add(match.group(1) or match.group(2))
    return found


def command_check_rules(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    with FileAssistantSession(args) as session:
        py_files = session.list_files(["*.py"], 300).get("files", [])
        env_example = session.read_file(".env.example", 120000)
        docs = [session.read_file(path, 80000) for path in ["README.md", "docs/PROJECT_COMMANDS.md", "docs/PROJECT_STRUCTURE.md"]]
        env_vars: set[str] = set()
        for path in py_files:
            try:
                item = session.read_file(path, 120000)
            except Exception:
                continue
            env_vars.update(extract_env_vars(str(item.get("content", ""))))
        env_text = str(env_example.get("content", ""))
        ignored_env = {"LOCALAPPDATA"}
        missing_env = sorted(var for var in env_vars if var not in env_text and var not in ignored_env)
        docs_text = "\n".join(str(item.get("content", "")) for item in docs)
        required_commands = [
            "day34_file_assistant.py mcp-tools",
            "day34_file_assistant.py find-usages",
            "day34_file_assistant.py update-docs",
            "day34_file_assistant.py generate-file",
            "day34_file_assistant.py prepare-diff",
        ]
        missing_docs = [cmd for cmd in required_commands if cmd not in docs_text]
        result = {
            "env_vars_found": sorted(env_vars),
            "missing_in_env_example": missing_env,
            "missing_doc_commands": missing_docs,
            "ok": not missing_env and not missing_docs,
        }
        trace = CommandTrace(
            command="check-rules",
            generated_at=now_iso(),
            inputs={},
            mcp_tools=session.tool_names,
            files_read=session.files_read,
            files_written=[],
            result=result,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            elapsed_seconds=round(time.perf_counter() - started, 3),
            llm_used=False,
        )
        save_trace(session.store_dir, "last_check_rules", trace)
        print("check_rules")
        print(to_json(result))
    return 0 if result["ok"] else 2


def command_prepare_diff(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    with FileAssistantSession(args) as session:
        diff = session.git_diff(args.paths or [], args.max_chars)
        trace = CommandTrace(
            command="prepare-diff",
            generated_at=now_iso(),
            inputs={"paths": args.paths or [], "max_chars": args.max_chars},
            mcp_tools=session.tool_names,
            files_read=[],
            files_written=[],
            result=diff,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            elapsed_seconds=round(time.perf_counter() - started, 3),
            llm_used=False,
        )
        save_trace(session.store_dir, "last_prepare_diff", trace)
        print("status:")
        for line in diff.get("status_short", []):
            print(line)
        print("diff:")
        print(diff.get("diff", ""))
    return 0


def render_report(store_dir: Path) -> str:
    trace_files = [
        "last_find_usages.json",
        "last_update_docs.json",
        "last_generate_file.json",
        "last_check_rules.json",
        "last_prepare_diff.json",
    ]
    traces = []
    for name in trace_files:
        path = store_dir / name
        if path.exists():
            traces.append(json.loads(path.read_text(encoding="utf-8")))
    lines = [
        "# Day34. Real Project File Assistant",
        "",
        f"- generated_at: `{now_iso()}`",
        f"- store_dir: `{store_dir}`",
        "",
        "## Real Operations",
        "",
    ]
    for trace in traces:
        lines.extend(
            [
                f"### {trace['command']}",
                "",
                f"- llm_used: `{trace['llm_used']}`",
                f"- files_read: `{', '.join(trace['files_read']) or '-'}`",
                f"- files_written: `{', '.join(trace['files_written']) or '-'}`",
                f"- tokens: `{trace['tokens']}`",
                f"- elapsed_seconds: `{trace['elapsed_seconds']}`",
                "",
            ]
        )
        result = trace.get("result", {})
        if trace["command"] == "find-usages":
            search = result.get("search", {})
            lines.extend([f"- matches: `{search.get('count')}`", "", result.get("analysis", "")[:4000], ""])
        elif "diff" in result:
            diff_value = result["diff"]
            diff = diff_value.get("diff", "") if isinstance(diff_value, dict) else str(diff_value)
            lines.extend(["```diff", diff[:12000], "```", ""])
        else:
            lines.extend(["```json", to_json(result)[:12000], "```", ""])
    lines.extend(
        [
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day34_file_assistant.py mcp-tools",
            ".\\.venv\\Scripts\\python.exe day34_file_assistant.py find-usages \"DEEPSEEK_API_KEY\"",
            ".\\.venv\\Scripts\\python.exe day34_file_assistant.py update-docs --target docs/PROJECT_COMMANDS.md --apply",
            ".\\.venv\\Scripts\\python.exe day34_file_assistant.py generate-file --kind readme --target docs/DAY34_FILE_ASSISTANT_USAGE.md --apply",
            ".\\.venv\\Scripts\\python.exe day34_file_assistant.py check-rules",
            ".\\.venv\\Scripts\\python.exe day34_file_assistant.py prepare-diff",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def command_report(args: argparse.Namespace) -> int:
    store_dir = store_dir_from_env(args.store_dir)
    report = render_report(store_dir)
    Path(DEFAULT_REPORT_PATH).write_text(report, encoding="utf-8")
    print(f"report_saved={Path(DEFAULT_REPORT_PATH).resolve()}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--store-dir")
    parser.add_argument("--mcp-url")
    parser.add_argument("--no-spawn-mcp", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day34 real project file assistant.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools_parser = subparsers.add_parser("mcp-tools")
    add_common_args(tools_parser)
    tools_parser.set_defaults(func=command_mcp_tools)

    find_parser = subparsers.add_parser("find-usages")
    find_parser.add_argument("query")
    find_parser.add_argument("--globs", nargs="*", default=DEFAULT_SEARCH_GLOBS)
    find_parser.add_argument("--max-results", type=int, default=200)
    add_common_args(find_parser)
    find_parser.set_defaults(func=command_find_usages)

    update_parser = subparsers.add_parser("update-docs")
    update_parser.add_argument("--target", default="docs/PROJECT_COMMANDS.md")
    update_parser.add_argument("--apply", action="store_true")
    add_common_args(update_parser)
    update_parser.set_defaults(func=command_update_docs)

    generate_parser = subparsers.add_parser("generate-file")
    generate_parser.add_argument("--kind", choices=["adr", "readme", "changelog"], required=True)
    generate_parser.add_argument("--target", required=True)
    generate_parser.add_argument("--apply", action="store_true")
    generate_parser.add_argument("--overwrite", action="store_true")
    add_common_args(generate_parser)
    generate_parser.set_defaults(func=command_generate_file)

    check_parser = subparsers.add_parser("check-rules")
    add_common_args(check_parser)
    check_parser.set_defaults(func=command_check_rules)

    diff_parser = subparsers.add_parser("prepare-diff")
    diff_parser.add_argument("--paths", nargs="*", default=[])
    diff_parser.add_argument("--max-chars", type=int, default=120000)
    add_common_args(diff_parser)
    diff_parser.set_defaults(func=command_prepare_diff)

    report_parser = subparsers.add_parser("report")
    add_common_args(report_parser)
    report_parser.set_defaults(func=command_report)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
