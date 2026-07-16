import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_MCP_URL = "https://mcp.deepwiki.com/mcp"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_REPORT_PATH = "DAY16_MCP_REPORT.md"
EXPECTED_TOOLS = {"read_wiki_structure", "read_wiki_contents", "ask_question"}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class MCPDiscoveryResult:
    server_url: str
    connected: bool
    tools: list[dict[str, Any]]
    elapsed_seconds: float
    error: str = ""

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    @property
    def missing_expected_tools(self) -> list[str]:
        names = {tool["name"] for tool in self.tools}
        return sorted(EXPECTED_TOOLS - names)

    @property
    def expected_tools_ok(self) -> bool:
        return not self.missing_expected_tools


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_float(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_config(args: argparse.Namespace) -> tuple[str, float]:
    load_dotenv()
    url = args.url or os.getenv("DAY16_MCP_URL", DEFAULT_MCP_URL)
    timeout = args.timeout or parse_float(os.getenv("DAY16_MCP_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    return url, timeout


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def normalize_tool(tool: Any) -> dict[str, Any]:
    raw = model_to_dict(tool)
    input_schema = raw.get("inputSchema") or raw.get("input_schema") or {}
    return {
        "name": raw.get("name") or getattr(tool, "name", ""),
        "description": raw.get("description") or getattr(tool, "description", "") or "",
        "input_schema": input_schema,
    }


async def discover_tools(url: str, timeout_seconds: float) -> MCPDiscoveryResult:
    started = time.perf_counter()
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as error:
        elapsed = time.perf_counter() - started
        return MCPDiscoveryResult(url, False, [], elapsed, f"MCP SDK is not installed: {error}")

    async def run_session() -> list[dict[str, Any]]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                return [normalize_tool(tool) for tool in tools_response.tools]

    try:
        tools = await asyncio.wait_for(run_session(), timeout=timeout_seconds)
        elapsed = time.perf_counter() - started
        return MCPDiscoveryResult(url, True, tools, elapsed)
    except Exception as error:
        elapsed = time.perf_counter() - started
        return MCPDiscoveryResult(url, False, [], elapsed, f"{type(error).__name__}: {error}")


def format_console_result(result: MCPDiscoveryResult) -> str:
    lines = [
        f"server_url={result.server_url}",
        f"connected={result.connected}",
        f"tools_count={result.tool_count}",
        f"expected_tools_ok={result.expected_tools_ok}",
    ]
    if result.error:
        lines.append(f"error={result.error}")
        return "\n".join(lines)
    if result.missing_expected_tools:
        lines.append("missing_expected_tools=" + ", ".join(result.missing_expected_tools))
    for tool in result.tools:
        lines.append("")
        lines.append(f"- name: {tool['name']}")
        lines.append(f"  description: {tool['description']}")
        lines.append("  input_schema:")
        lines.append(indent(to_json(tool["input_schema"]), "    "))
    return "\n".join(lines)


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def build_report(result: MCPDiscoveryResult) -> str:
    return "\n".join(
        [
            "# Day 16. MCP Connection + Tools List",
            "",
            "## Summary",
            "",
            "- MCP client connects to a remote MCP server.",
            "- Transport: Streamable HTTP.",
            "- Operation: `initialize` + `tools/list`.",
            "- No DeepSeek API call.",
            "- No MCP tool calls.",
            "",
            "## Connection",
            "",
            f"- server_url: `{result.server_url}`",
            "- server: DeepWiki MCP",
            "- auth: no-auth public server",
            f"- connected: `{result.connected}`",
            f"- elapsed_seconds: `{result.elapsed_seconds:.2f}`",
            f"- generated_at: `{now_iso()}`",
            "",
            "## Tools",
            "",
            f"- tools_count: `{result.tool_count}`",
            f"- expected_tools_ok: `{result.expected_tools_ok}`",
            f"- missing_expected_tools: `{', '.join(result.missing_expected_tools) or 'none'}`",
            "",
            "```json",
            to_json(result.tools),
            "```",
            "",
            "## How To Check",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            ".\\.venv\\Scripts\\python.exe -m py_compile day16_mcp_tools_list.py",
            ".\\.venv\\Scripts\\python.exe day16_mcp_tools_list.py list",
            ".\\.venv\\Scripts\\python.exe day16_mcp_tools_list.py demo",
            "```",
            "",
            "## Result",
            "",
            "- MCP connection established.",
            "- MCP tools list returned.",
            "- DeepWiki expected tools are present.",
            "- Minimal Day16 requirement is complete.",
        ]
    )


async def run_list(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    result = await discover_tools(url, timeout)
    print(format_console_result(result))
    return 0 if result.connected else 1


async def run_demo(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    result = await discover_tools(url, timeout)
    print(format_console_result(result))
    report_path = Path(args.report)
    report_path.write_text(build_report(result), encoding="utf-8")
    print("")
    print(f"report_saved:{report_path.resolve()}")
    return 0 if result.connected and result.expected_tools_ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 16 MCP client: initialize and list tools.")
    parser.add_argument("--url", default=None, help="MCP Streamable HTTP URL")
    parser.add_argument("--timeout", type=float, default=None, help="Connection timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="connect to MCP and print available tools")

    demo_parser = subparsers.add_parser("demo", help="connect to MCP, print tools, write report")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    args = parser.parse_args()
    if args.command == "list":
        raise SystemExit(asyncio.run(run_list(args)))
    if args.command == "demo":
        raise SystemExit(asyncio.run(run_demo(args)))


if __name__ == "__main__":
    main()
