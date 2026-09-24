"""Backward-compatible import path.

The Canva capability implementation is the adapter, which talks to Canva's
official remote MCP server and discovers tools per user.
"""

from backend.integrations.canva.adapter import CanvaAdapter, get_canva_client

CanvaClient = CanvaAdapter

__all__ = ["CanvaAdapter", "CanvaClient", "get_canva_client"]
