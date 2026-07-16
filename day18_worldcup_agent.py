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
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from memory_layers_agent import (
    DEEPSEEK_BASE_URL,
    DEFAULT_API_RETRIES,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    THINKING_DISABLED,
    parse_float,
    parse_int,
    require_env,
    usage_value,
)


DEFAULT_MCP_URL = "http://138.16.168.37:8002/mcp"
DEFAULT_REPORT_PATH = "DAY18_WORLDCUP_SCHEDULER_REPORT.md"
EXPECTED_TOOL = "get_worldcup_match_summary"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class SummaryResult:
    server_url: str
    tool_name: str
    result: dict[str, Any]
    elapsed_seconds: float


@dataclass(frozen=True)
class AgentResult:
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    attempts: int
    error: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_config(args: argparse.Namespace) -> tuple[str, float]:
    load_dotenv()
    url = args.url or os.getenv("DAY18_MCP_URL", DEFAULT_MCP_URL)
    timeout = args.timeout or parse_float(os.getenv("DAY18_MCP_TIMEOUT_SECONDS"), 20.0)
    return url, timeout


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def normalize_tool(tool: Any) -> ToolInfo:
    raw = model_to_dict(tool)
    return ToolInfo(
        name=raw.get("name") or getattr(tool, "name", ""),
        description=raw.get("description") or getattr(tool, "description", "") or "",
        input_schema=raw.get("inputSchema") or raw.get("input_schema") or {},
    )


def extract_json_from_call_result(call_result: Any) -> dict[str, Any]:
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
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return raw


async def list_remote_tools(url: str, timeout_seconds: float) -> list[ToolInfo]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> list[ToolInfo]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                return [normalize_tool(tool) for tool in tools_response.tools]

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


async def call_worldcup_summary(url: str, timeout_seconds: float, include_matches: bool, force_refresh: bool) -> SummaryResult:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    started = time.perf_counter()

    async def run_session() -> dict[str, Any]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                call_result = await session.call_tool(
                    EXPECTED_TOOL,
                    {"include_matches": include_matches, "force_refresh": force_refresh},
                )
                return extract_json_from_call_result(call_result)

    result = await asyncio.wait_for(run_session(), timeout=timeout_seconds)
    return SummaryResult(url, EXPECTED_TOOL, result, time.perf_counter() - started)


def deepseek_client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def ask_deepseek(summary: SummaryResult) -> AgentResult:
    client, model, max_attempts = deepseek_client_from_env()
    messages = [
        {
            "role": "system",
            "content": (
                "Ты футбольный агент-сводчик. Используй только MCP-result ниже. "
                "Ответь по-русски: что уже сыграли, что идет/будет, что важно. Коротко."
            ),
        },
        {
            "role": "user",
            "content": (
                "Сделай сводку матчей чемпионата мира по футболу на основе результата scheduled MCP tool.\n\n"
                f"MCP server: {summary.server_url}\n"
                f"MCP tool: {summary.tool_name}\n"
                f"MCP result JSON:\n{to_json(summary.result)}"
            ),
        },
    ]
    started = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                extra_body=THINKING_DISABLED,
                temperature=0,
                max_tokens=600,
                messages=messages,
            )
            usage = response.usage
            answer = response.choices[0].message.content.strip() if response.choices else ""
            return AgentResult(
                answer=answer,
                prompt_tokens=usage_value(usage, "prompt_tokens"),
                completion_tokens=usage_value(usage, "completion_tokens"),
                total_tokens=usage_value(usage, "total_tokens"),
                elapsed_seconds=time.perf_counter() - started,
                attempts=attempt,
            )
        except APIStatusError as error:
            if (error.status_code not in {408, 429} and error.status_code < 500) or attempt == max_attempts:
                return AgentResult("", 0, 0, 0, time.perf_counter() - started, attempt, str(error))
        except (APITimeoutError, APIConnectionError, APIError) as error:
            if attempt == max_attempts:
                return AgentResult("", 0, 0, 0, time.perf_counter() - started, attempt, str(error))
        time.sleep(min(2 * attempt, 6))
    return AgentResult("", 0, 0, 0, time.perf_counter() - started, max_attempts, "DeepSeek request failed")


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def format_tools(url: str, tools: list[ToolInfo]) -> str:
    lines = [
        f"server_url={url}",
        "connected=True",
        f"tools_count={len(tools)}",
        f"expected_tool_present={any(tool.name == EXPECTED_TOOL for tool in tools)}",
    ]
    for tool in tools:
        lines.extend(["", f"- name: {tool.name}", f"  description: {tool.description}", "  input_schema:", indent(to_json(tool.input_schema), "    ")])
    return "\n".join(lines)


def format_summary(summary: SummaryResult) -> str:
    scheduler = summary.result.get("scheduler", {})
    aggregate = summary.result.get("aggregate", {})
    return "\n".join(
        [
            f"server_url={summary.server_url}",
            "connected=True",
            f"tool_called={summary.tool_name}",
            f"scheduler_enabled={scheduler.get('enabled')}",
            f"refresh_seconds={scheduler.get('refresh_seconds')}",
            f"last_fetched_at={scheduler.get('last_fetched_at')}",
            f"force_refresh_used={scheduler.get('force_refresh_used')}",
            f"elapsed_seconds={summary.elapsed_seconds:.2f}",
            "aggregate:",
            indent(to_json(aggregate), "  "),
        ]
    )


def format_agent(result: AgentResult) -> str:
    if result.error:
        return f"deepseek_error={result.error}"
    return "\n".join(
        [
            "deepseek_used=True",
            f"tokens prompt={result.prompt_tokens} completion={result.completion_tokens} total={result.total_tokens}",
            f"elapsed_seconds={result.elapsed_seconds:.2f}",
            "agent_answer:",
            result.answer,
        ]
    )


def build_report(url: str, tools: list[ToolInfo], summary: SummaryResult, agent: AgentResult) -> str:
    return "\n".join(
        [
            "# Day 18. MCP Scheduler + Background World Cup Summary",
            "",
            "## Summary",
            "",
            "- Remote MCP server runs 24/7 under systemd.",
            "- Background scheduler fetches FIFA World Cup scoreboard data periodically.",
            "- Server stores snapshots in JSON.",
            "- MCP tool returns aggregated match summary.",
            "- Agent sends MCP result to DeepSeek and gets a human summary.",
            "",
            "## Remote MCP",
            "",
            f"- url: `{url}`",
            "- connected: `True`",
            f"- generated_at: `{now_iso()}`",
            f"- tools_count: `{len(tools)}`",
            f"- expected_tool_present: `{any(tool.name == EXPECTED_TOOL for tool in tools)}`",
            "",
            "## Tool Schema",
            "",
            "```json",
            to_json([tool.__dict__ for tool in tools]),
            "```",
            "",
            "## Scheduled Tool Result",
            "",
            "```json",
            to_json(summary.result),
            "```",
            "",
            "## DeepSeek Summary",
            "",
            f"- error: `{agent.error or 'none'}`",
            f"- tokens: prompt={agent.prompt_tokens}, completion={agent.completion_tokens}, total={agent.total_tokens}",
            "",
            "```text",
            agent.answer,
            "```",
            "",
            "## How To Check",
            "",
            "```powershell",
".\\.venv\\Scripts\\python.exe day18_worldcup_agent.py tools --url http://138.16.168.37:8002/mcp",
".\\.venv\\Scripts\\python.exe day18_worldcup_agent.py summary --force-refresh --url http://138.16.168.37:8002/mcp",
".\\.venv\\Scripts\\python.exe day18_worldcup_agent.py ask --force-refresh --url http://138.16.168.37:8002/mcp",
            "```",
        ]
    )


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=None, help="Remote MCP Streamable HTTP URL")
    parser.add_argument("--timeout", type=float, default=None, help="MCP timeout in seconds")


async def run_tools(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    print(format_tools(url, tools))
    return 0 if any(tool.name == EXPECTED_TOOL for tool in tools) else 1


async def run_summary(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    summary = await call_worldcup_summary(url, timeout, args.include_matches, args.force_refresh)
    print(format_summary(summary))
    return 0


async def run_demo(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    print(format_tools(url, tools))
    print("")
    summary = await call_worldcup_summary(url, timeout, args.include_matches, args.force_refresh)
    print(format_summary(summary))
    print("")
    agent = ask_deepseek(summary)
    print(format_agent(agent))
    report_path = Path(args.report)
    report_path.write_text(build_report(url, tools, summary, agent), encoding="utf-8")
    print("")
    print(f"report_saved:{report_path.resolve()}")
    return 0 if not agent.error else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 18 agent for scheduled World Cup MCP summaries.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools_parser = subparsers.add_parser("tools", help="list remote MCP tools")
    add_connection_args(tools_parser)

    summary_parser = subparsers.add_parser("summary", help="get aggregated World Cup summary from MCP store")
    add_connection_args(summary_parser)
    summary_parser.add_argument("--include-matches", action="store_true", help="include normalized matches")
    summary_parser.add_argument("--force-refresh", action="store_true", help="fetch ESPN data now before returning")

    ask_parser = subparsers.add_parser("ask", help="get real MCP data and ask DeepSeek for a World Cup summary")
    add_connection_args(ask_parser)
    ask_parser.add_argument("--include-matches", action="store_true", help="include normalized matches")
    ask_parser.add_argument("--force-refresh", action="store_true", help="fetch ESPN data now before returning")
    ask_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    demo_parser = subparsers.add_parser("demo", help="get scheduled MCP summary and ask DeepSeek to summarize")
    add_connection_args(demo_parser)
    demo_parser.add_argument("--include-matches", action="store_true", help="include normalized matches")
    demo_parser.add_argument("--force-refresh", action="store_true", help="fetch ESPN data now before returning")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    args = parser.parse_args()
    if args.command == "tools":
        raise SystemExit(asyncio.run(run_tools(args)))
    if args.command == "summary":
        raise SystemExit(asyncio.run(run_summary(args)))
    if args.command == "ask":
        raise SystemExit(asyncio.run(run_demo(args)))
    if args.command == "demo":
        raise SystemExit(asyncio.run(run_demo(args)))


if __name__ == "__main__":
    main()
