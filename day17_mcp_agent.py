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


DEFAULT_MCP_URL = "http://138.16.168.37:8000/mcp"
DEFAULT_REPORT_PATH = "DAY17_REMOTE_MCP_REPORT.md"
DEFAULT_REPO = "modelcontextprotocol/python-sdk"
EXPECTED_TOOL = "get_github_repo_summary"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResult:
    server_url: str
    repo: str
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
    url = args.url or os.getenv("DAY17_MCP_URL", DEFAULT_MCP_URL)
    timeout = args.timeout or parse_float(os.getenv("DAY17_MCP_TIMEOUT_SECONDS"), 20.0)
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


def parse_repo(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("repo must use owner/repo format")
    return parts[0], parts[1]


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
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            return parsed
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


async def call_repo_summary_tool(url: str, timeout_seconds: float, repo: str) -> ToolCallResult:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    owner, repo_name = parse_repo(repo)
    started = time.perf_counter()

    async def run_session() -> dict[str, Any]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                call_result = await session.call_tool(EXPECTED_TOOL, {"owner": owner, "repo": repo_name})
                return extract_json_from_call_result(call_result)

    result = await asyncio.wait_for(run_session(), timeout=timeout_seconds)
    return ToolCallResult(url, f"{owner}/{repo_name}", EXPECTED_TOOL, result, time.perf_counter() - started)


def deepseek_client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def ask_deepseek_with_tool_result(tool_result: ToolCallResult) -> AgentResult:
    client, model, max_attempts = deepseek_client_from_env()
    messages = [
        {
            "role": "system",
            "content": (
                "Ты агент, который использует результат MCP-инструмента. "
                "Отвечай по-русски, коротко, и явно укажи, какие данные пришли из MCP tool."
            ),
        },
        {
            "role": "user",
            "content": (
                "Сделай краткий вывод по GitHub репозиторию на основе результата MCP tool.\n\n"
                f"MCP server: {tool_result.server_url}\n"
                f"MCP tool: {tool_result.tool_name}\n"
                f"Repository: {tool_result.repo}\n"
                f"Tool result JSON:\n{to_json(tool_result.result)}"
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
                max_tokens=500,
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


def format_tools(url: str, tools: list[ToolInfo]) -> str:
    lines = [
        f"server_url={url}",
        "connected=True",
        f"tools_count={len(tools)}",
        f"expected_tool_present={any(tool.name == EXPECTED_TOOL for tool in tools)}",
    ]
    for tool in tools:
        lines.extend(
            [
                "",
                f"- name: {tool.name}",
                f"  description: {tool.description}",
                "  input_schema:",
                indent(to_json(tool.input_schema), "    "),
            ]
        )
    return "\n".join(lines)


def format_tool_call(result: ToolCallResult) -> str:
    return "\n".join(
        [
            f"server_url={result.server_url}",
            "connected=True",
            f"tool_called={result.tool_name}",
            f"repo={result.repo}",
            f"elapsed_seconds={result.elapsed_seconds:.2f}",
            "tool_result:",
            indent(to_json(result.result), "  "),
        ]
    )


def format_agent_result(result: AgentResult) -> str:
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


def build_report(url: str, tools: list[ToolInfo], call_result: ToolCallResult, agent_result: AgentResult) -> str:
    return "\n".join(
        [
            "# Day 17. Remote MCP Server + Agent Tool Call",
            "",
            "## Summary",
            "",
            "- Custom MCP server is deployed remotely on the VPS.",
            "- Transport: Streamable HTTP.",
            "- External API: GitHub public REST API.",
            "- MCP tool: `get_github_repo_summary`.",
            "- Agent calls the MCP tool and sends the result to DeepSeek.",
            "",
            "## Remote MCP Connection",
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
            "## Tool Call Result",
            "",
            "```json",
            to_json(call_result.result),
            "```",
            "",
            "## Agent Result",
            "",
            f"- DeepSeek error: `{agent_result.error or 'none'}`",
            f"- tokens: prompt={agent_result.prompt_tokens}, completion={agent_result.completion_tokens}, total={agent_result.total_tokens}",
            "",
            "```text",
            agent_result.answer,
            "```",
            "",
            "## How To Check",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day17_mcp_agent.py tools --url http://138.16.168.37:8000/mcp",
            ".\\.venv\\Scripts\\python.exe day17_mcp_agent.py call --repo modelcontextprotocol/python-sdk --url http://138.16.168.37:8000/mcp",
            ".\\.venv\\Scripts\\python.exe day17_mcp_agent.py demo --repo modelcontextprotocol/python-sdk --url http://138.16.168.37:8000/mcp",
            "```",
        ]
    )


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=None, help="Remote MCP Streamable HTTP URL")
    parser.add_argument("--timeout", type=float, default=None, help="MCP timeout in seconds")


async def run_tools(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    print(format_tools(url, tools))
    return 0 if any(tool.name == EXPECTED_TOOL for tool in tools) else 1


async def run_call(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    result = await call_repo_summary_tool(url, timeout, args.repo)
    print(format_tool_call(result))
    return 0


async def run_demo(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    print(format_tools(url, tools))
    print("")
    call_result = await call_repo_summary_tool(url, timeout, args.repo)
    print(format_tool_call(call_result))
    print("")
    agent_result = ask_deepseek_with_tool_result(call_result)
    print(format_agent_result(agent_result))
    report_path = Path(args.report)
    report_path.write_text(build_report(url, tools, call_result, agent_result), encoding="utf-8")
    print("")
    print(f"report_saved:{report_path.resolve()}")
    return 0 if not agent_result.error else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 17 agent that calls a remote MCP tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools_parser = subparsers.add_parser("tools", help="list remote MCP tools")
    add_connection_args(tools_parser)

    call_parser = subparsers.add_parser("call", help="call get_github_repo_summary")
    add_connection_args(call_parser)
    call_parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in owner/repo format")

    demo_parser = subparsers.add_parser("demo", help="call MCP tool and ask DeepSeek to use the result")
    add_connection_args(demo_parser)
    demo_parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in owner/repo format")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    args = parser.parse_args()
    if args.command == "tools":
        raise SystemExit(asyncio.run(run_tools(args)))
    if args.command == "call":
        raise SystemExit(asyncio.run(run_call(args)))
    if args.command == "demo":
        raise SystemExit(asyncio.run(run_demo(args)))


if __name__ == "__main__":
    main()
