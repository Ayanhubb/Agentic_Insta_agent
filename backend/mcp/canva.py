"""Shim so the scheduler can load the optional Canva adapter."""

from backend.integrations.canva.adapter import CanvaAdapter, get_canva_client

CanvaClient = CanvaAdapter

__all__ = ["CanvaAdapter", "CanvaClient", "get_canva_client"]
