"""Indian festival catalog and campaign helpers.

Named ``festivals`` so it never shadows the standard library ``calendar`` module.
"""

from festivals.festival_service import FestivalService
from festivals.india_festivals import festivals_for_year

__all__ = ["FestivalService", "festivals_for_year"]
