from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8034
DEFAULT_CRM_JSON = "day33_support_crm.json"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_crm(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"CRM JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("users"), list) or not isinstance(data.get("tickets"), list):
        raise ValueError("CRM JSON must contain users[] and tickets[]")
    return data


def normalize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9_./:-]+", text)
        if len(token) > 1
    }


def text_blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {text_blob(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(text_blob(item) for item in value)
    return str(value)


def find_user(crm: dict[str, Any], user_id: str) -> dict[str, Any] | None:
    for user in crm["users"]:
        if str(user.get("user_id")) == user_id:
            return user
    return None


def find_ticket(crm: dict[str, Any], ticket_id: str) -> dict[str, Any] | None:
    for ticket in crm["tickets"]:
        if str(ticket.get("ticket_id")) == ticket_id:
            return ticket
    return None


def tickets_for_user(crm: dict[str, Any], user_id: str) -> list[dict[str, Any]]:
    return [ticket for ticket in crm["tickets"] if str(ticket.get("user_id")) == user_id]


def score_ticket(ticket: dict[str, Any], query_tokens: set[str]) -> int:
    ticket_tokens = normalize(text_blob(ticket))
    return len(query_tokens & ticket_tokens)


def create_mcp_server(crm_path: Path, host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        name="day33-support-crm-mcp",
        instructions="Read-only MCP server exposing users and support tickets from a JSON CRM file.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="get_user_profile",
        description="Return one support user profile from the JSON CRM by user_id.",
        structured_output=True,
    )
    async def get_user_profile(
        user_id: Annotated[str, Field(description="CRM user id, for example u1001.", min_length=1, max_length=64)],
    ) -> dict[str, Any]:
        crm = load_crm(crm_path)
        user = find_user(crm, user_id)
        if not user:
            return {"found": False, "user_id": user_id, "user": None, "tickets": []}
        tickets = tickets_for_user(crm, user_id)
        return {
            "found": True,
            "user_id": user_id,
            "user": user,
            "tickets": [
                {
                    "ticket_id": ticket.get("ticket_id"),
                    "status": ticket.get("status"),
                    "priority": ticket.get("priority"),
                    "subject": ticket.get("subject"),
                    "last_event_at": ticket.get("last_event_at"),
                }
                for ticket in tickets
            ],
        }

    @mcp.tool(
        name="get_ticket",
        description="Return one support ticket with events and matching user profile.",
        structured_output=True,
    )
    async def get_ticket(
        ticket_id: Annotated[str, Field(description="Support ticket id, for example t9001.", min_length=1, max_length=64)],
    ) -> dict[str, Any]:
        crm = load_crm(crm_path)
        ticket = find_ticket(crm, ticket_id)
        if not ticket:
            return {"found": False, "ticket_id": ticket_id, "ticket": None, "user": None}
        return {
            "found": True,
            "ticket_id": ticket_id,
            "ticket": ticket,
            "user": find_user(crm, str(ticket.get("user_id"))),
        }

    @mcp.tool(
        name="search_support_tickets",
        description="Search support tickets in the JSON CRM by words from a user question.",
        structured_output=True,
    )
    async def search_support_tickets(
        query: Annotated[str, Field(description="Natural language search query.", min_length=1, max_length=1000)],
        status: Annotated[str, Field(description="Optional ticket status filter: open, resolved, or empty.", max_length=32)] = "",
        limit: Annotated[int, Field(description="Maximum tickets to return.", ge=1, le=20)] = 5,
    ) -> dict[str, Any]:
        crm = load_crm(crm_path)
        query_tokens = normalize(query)
        scored = []
        for ticket in crm["tickets"]:
            if status and str(ticket.get("status")) != status:
                continue
            score = score_ticket(ticket, query_tokens)
            if score > 0:
                scored.append((score, ticket))
        scored.sort(key=lambda item: item[0], reverse=True)
        return {
            "query": query,
            "status_filter": status,
            "matches": [
                {
                    "score": score,
                    "ticket_id": ticket.get("ticket_id"),
                    "user_id": ticket.get("user_id"),
                    "status": ticket.get("status"),
                    "priority": ticket.get("priority"),
                    "subject": ticket.get("subject"),
                    "description": ticket.get("description"),
                    "tags": ticket.get("tags", []),
                }
                for score, ticket in scored[:limit]
            ],
        }

    @mcp.tool(
        name="list_open_tickets",
        description="Return current open tickets from the JSON CRM.",
        structured_output=True,
    )
    async def list_open_tickets(
        limit: Annotated[int, Field(description="Maximum tickets to return.", ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        crm = load_crm(crm_path)
        open_tickets = [ticket for ticket in crm["tickets"] if ticket.get("status") == "open"]
        return {
            "count": len(open_tickets),
            "tickets": [
                {
                    "ticket_id": ticket.get("ticket_id"),
                    "user_id": ticket.get("user_id"),
                    "priority": ticket.get("priority"),
                    "subject": ticket.get("subject"),
                    "last_event_at": ticket.get("last_event_at"),
                }
                for ticket in open_tickets[:limit]
            ],
        }

    return mcp


def print_tool_schema(crm_path: Path, host: str, port: int) -> None:
    server = create_mcp_server(crm_path, host, port)
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
    parser = argparse.ArgumentParser(description="Day33 JSON CRM MCP server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument("--crm-json", default=DEFAULT_CRM_JSON)

    schema_parser = subparsers.add_parser("schema")
    schema_parser.add_argument("--host", default=DEFAULT_HOST)
    schema_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    schema_parser.add_argument("--crm-json", default=DEFAULT_CRM_JSON)

    args = parser.parse_args()
    crm_path = Path(args.crm_json).resolve()
    if args.command == "serve":
        load_crm(crm_path)
        server = create_mcp_server(crm_path, args.host, args.port)
        print(f"starting day33-support-crm-mcp on {args.host}:{args.port}/mcp crm={crm_path}", flush=True)
        server.run(transport="streamable-http")
    elif args.command == "schema":
        print_tool_schema(crm_path, args.host, args.port)


if __name__ == "__main__":
    main()
