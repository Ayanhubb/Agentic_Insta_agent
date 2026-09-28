"""Which asset roles one agent version may resolve. An empty set is valid."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from backend.platform.assets.catalog import INSTAGRAM_ASSET_ROLES, AssetRole
from backend.platform.assets.errors import fail

_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class AssetBinding:
    """Roles granted to an agent version. The binding does not hold bytes or paths."""

    agent_id: str
    version: int
    roles: frozenset[AssetRole]

    def grants(self, role: AssetRole) -> bool:
        return role in self.roles


def bind_assets(agent_id: str, version: int, roles: Iterable[str]) -> AssetBinding:
    """Bind an agent version to capability roles. The set may be empty."""
    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        raise fail("INVALID_DEFINITION")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise fail("INVALID_DEFINITION")
    if isinstance(roles, str) or not isinstance(roles, (set, frozenset, list, tuple)):
        raise fail("INVALID_DEFINITION")
    resolved: set[AssetRole] = set()
    for role in roles:
        try:
            resolved.add(role if isinstance(role, AssetRole) else AssetRole(str(role)))
        except ValueError as exc:
            raise fail("INVALID_DEFINITION") from exc
    return AssetBinding(agent_id=agent_id, version=version, roles=frozenset(resolved))


def instagram_marketing_assets(version: int = 1) -> AssetBinding:
    """Reference agent. These roles are optional files, not startup requirements."""
    return AssetBinding(
        agent_id="instagram_marketing",
        version=version,
        roles=INSTAGRAM_ASSET_ROLES,
    )
