"""Capability servers registered on the MCP allowlist."""

from __future__ import annotations

from backend.mcp.registry import MCPRegistry
from backend.mcp.servers.asset import asset_tools
from backend.mcp.servers.brand import brand_tools
from backend.mcp.servers.business import business_tools
from backend.mcp.servers.content import content_tools
from backend.mcp.servers.festival import festival_tools
from backend.mcp.servers.product import product_tools
from backend.mcp.sources import RepositoryGateway


def build_registry(gateway: RepositoryGateway) -> MCPRegistry:
    registry = MCPRegistry()
    for tool in (
        *business_tools(gateway),
        *brand_tools(gateway),
        *product_tools(gateway),
        *asset_tools(gateway),
        *festival_tools(gateway),
        *content_tools(gateway),
    ):
        registry.register(tool)
    return registry
