"""Optional Festival MCP, DeepSeek Vision, and Canva clients.

Missing integrations stay disabled. Factories are imported only when another
package has registered them. Failures are logged without exception text so
provider keys cannot land in the log line.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

_FESTIVAL_MCP = (
    ("festivals.mcp", "get_festival_mcp"),
    ("mcp.festival", "get_festival_mcp"),
    ("backend.mcp.festival", "get_festival_mcp"),
)
_VISION = (
    ("ai.vision.deepseek", "get_vision_provider"),
    ("backend.ai.vision.deepseek", "get_vision_provider"),
)
_CANVA = (
    ("backend.integrations.canva", "get_canva_client"),
    ("mcp.canva", "get_canva_client"),
    ("backend.mcp.canva", "get_canva_client"),
)


def _load_factory(candidates: tuple[tuple[str, str], ...], settings: Any) -> Any | None:
    for module_name, factory_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        factory = getattr(module, factory_name, None)
        if not callable(factory):
            continue
        try:
            return factory(settings)
        except Exception:
            logger.warning("Optional integration factory failed", extra={"step": module_name})
            return None
    return None


def resolve_festival_mcp(settings: Any) -> Any | None:
    return _load_factory(_FESTIVAL_MCP, settings)


def resolve_vision(settings: Any) -> Any | None:
    return _load_factory(_VISION, settings)


def resolve_canva(settings: Any) -> Any | None:
    return _load_factory(_CANVA, settings)
