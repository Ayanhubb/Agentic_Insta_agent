"""Capability servers registered on the MCP allowlist."""

from __future__ import annotations

from typing import Any

from backend.mcp.registry import MCPRegistry
from backend.mcp.servers.account import account_intelligence_tools
from backend.mcp.servers.asset import asset_tools
from backend.mcp.servers.brand import brand_tools
from backend.mcp.servers.business import business_tools
from backend.mcp.servers.content import content_tools
from backend.mcp.servers.festival import festival_tools
from backend.mcp.servers.product import product_tools
from backend.mcp.servers.trend import trend_tools
from backend.mcp.sources import RepositoryGateway
from config import Settings


def build_registry(
    gateway: RepositoryGateway,
    *,
    settings: Settings | None = None,
    reader: Any | None = None,
) -> MCPRegistry:
    registry = MCPRegistry()
    for tool in (
        *business_tools(gateway),
        *brand_tools(gateway),
        *product_tools(gateway),
        *asset_tools(gateway),
        *festival_tools(gateway),
        *content_tools(gateway),
        *account_intelligence_tools(gateway, settings=settings, reader=reader),
        *trend_tools(gateway),
    ):
        registry.register(tool)
    return registry
