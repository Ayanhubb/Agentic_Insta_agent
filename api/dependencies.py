"""FastAPI dependency injection."""

from __future__ import annotations

from functools import lru_cache

from agent.agent import InstagramAgent, build_instagram_agent
from api.security import CurrentUser, get_current_user
from config import Settings, get_settings


@lru_cache
def get_agent() -> InstagramAgent:
    settings: Settings = get_settings()
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return build_instagram_agent(settings)
