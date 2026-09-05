#!/usr/bin/env python3
"""
JARVIS MCP Server — Real Model Context Protocol implementation.
Exposes all JARVIS capabilities as MCP tools/resources for any client.
"""
import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Add project root to path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from ai_service import JarvisService
from auth import google_workspace
from desktop_controller import control_pc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis.mcp")

# ──────────────────────────────────────────────────────────────────────────────
# JARVIS Service singleton
# ──────────────────────────────────────────────────────────────────────────────

_jarvis: Optional[JarvisService] = None


def get_jarvis() -> JarvisService:
    global _jarvis
    if _jarvis is None:
        _jarvis = JarvisService()
    return _jarvis


# ──────────────────────────────────────────────────────────────────────────────
# MCP Server
# ──────────────────────────────────────────────────────────────────────────────

server = Server("jarvis")


@server.list_tools()
async def list_tools() -> List[types.Tool]:
    """Expose all JARVIS capabilities as MCP tools."""
    j = get_jarvis()
    tools = []

    # Core tools
    for spec in j.tools():
        fn = spec.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        tools.append(types.Tool(
            name=f"jarvis_{name}",
            description=fn.get("description", ""),
            inputSchema=fn.get("parameters", {"type": "object", "properties": {}})
        ))

    # Agent-specific tools
    tools.extend([
        types.Tool(
            name="jarvis_spawn_agent",
            description="Spawn a specialized agent (recruiter_ryan, invoice_ivy, lead_hunter, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent identifier"},
                    "task": {"type": "string", "description": "Task description"},
                    "autonomy": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"}
                },
                "required": ["agent_id", "task"]
            }
        ),
        types.Tool(
            name="jarvis_agent_status",
            description="Get status of a running agent",
            inputSchema={
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"]
            }
        ),
        types.Tool(
            name="jarvis_list_agents",
            description="List all available specialized agents",
            inputSchema={"type": "object", "properties": {}}
        ),
    ])

    return tools


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Execute a JARVIS tool via MCP."""
    j = get_jarvis()

    # Handle agent tools
    if name == "jarvis_spawn_agent":
        agent_id = arguments["agent_id"]
        task = arguments["task"]
        autonomy = arguments.get("autonomy", "medium")
        result = j.spawn_agent(agent_id, task, autonomy)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "jarvis_agent_status":
        agent_id = arguments["agent_id"]
        result = j.agent_status(agent_id)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "jarvis_list_agents":
        return [types.TextContent(type="text", text=json.dumps(j.list_agents(), indent=2))]

    # Core JARVIS tools - delegate to ai_service
    core_name = name.replace("jarvis_", "")
    try:
        output, extra = get_jarvis().call_tool(core_name, arguments, "mcp_session", [])
        return [types.TextContent(type="text", text=str(output))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


@server.list_resources()
async def list_resources() -> List[types.Resource]:
    """Expose JARVIS state as MCP resources."""
    j = get_jarvis()
    return [
        types.Resource(
            uri="jarvis://memory",
            name="JARVIS Memory",
            description="Long-term memory entries",
            mimeType="application/json"
        ),
        types.Resource(
            uri="jarvis://sessions",
            name="Active Sessions",
            description="Current conversation sessions",
            mimeType="application/json"
        ),
        types.Resource(
            uri="jarvis://skills",
            name="Installed Skills",
            description="Available skills",
            mimeType="application/json"
        ),
        types.Resource(
            uri="jarvis://agents",
            name="Specialized Agents",
            description="Available money-making agents",
            mimeType="application/json"
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    j = get_jarvis()
    if uri == "jarvis://memory":
        return json.dumps({"count": j.memory.count(), "entries": j.memory.search("", limit=50)}, indent=2)
    if uri == "jarvis://sessions":
        return json.dumps({"sessions": list(j.sessions.keys())}, indent=2)
    if uri == "jarvis://skills":
        return json.dumps({"skills": j.skills.all()}, indent=2)
    if uri == "jarvis://agents":
        agents = [
            {"id": "recruiter_ryan", "name": "Recruiter Ryan", "specialty": "Upwork/LinkedIn sourcing"},
            {"id": "invoice_ivy", "name": "Invoice Ivy", "specialty": "Invoice scanning, chasing, reconciliation"},
            {"id": "lead_hunter", "name": "Lead Hunter", "specialty": "IG/LinkedIn lead mining"},
            {"id": "inbox_zero", "name": "Inbox Zero", "specialty": "Cross-platform triage"},
        ]
        return json.dumps(agents, indent=2)
    raise ValueError(f"Unknown resource: {uri}")


async def main():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())