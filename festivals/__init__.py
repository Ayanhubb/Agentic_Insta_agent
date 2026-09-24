"""Indian festival catalog and campaign helpers.

Named ``festivals`` so it never shadows the standard library ``calendar`` module.
"""

from festivals.festival_service import FestivalService
from festivals.india_festivals import festivals_for_year
from festivals.mcp_tools import TOOL_NAMES, FestivalToolServer

__all__ = ["TOOL_NAMES", "FestivalService", "FestivalToolServer", "festivals_for_year"]
