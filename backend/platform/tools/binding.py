"""Agent grants into the tool registry. A grant does not register a tool."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.platform.tools.errors import fail
from backend.platform.tools.registry import PlatformToolRegistry

GRANTABLE_PERMISSIONS = frozenset({"read", "write"})


@dataclass(frozen=True, slots=True)
class ToolBinding:
    """Tools and permissions an agent may ask the registry to run."""

    agent_id: str
    tool_names: frozenset[str]
    permissions: frozenset[str]


def bind_agent(
    registry: PlatformToolRegistry,
    *,
    agent_id: str,
    tool_names: Iterable[str],
    permissions: Iterable[str],
) -> ToolBinding:
    """Bind an agent to tools that are already registered.

    Instagram publishing names are rejected. A write-classified tool is included
    only when the grant carries the write permission.
    """
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise fail("INVALID_DEFINITION", "Agent id is required.")
    if isinstance(permissions, str) or not isinstance(permissions, (set, frozenset, list, tuple)):
        raise fail("INVALID_DEFINITION", "Tool binding permissions are invalid.")
    granted_permissions = frozenset(permissions)
    if not granted_permissions or not granted_permissions <= GRANTABLE_PERMISSIONS:
        raise fail("INVALID_DEFINITION", "Tool binding permissions are invalid.")
    if isinstance(tool_names, str):
        raise fail("INVALID_DEFINITION", "Tool binding needs registered tool names.")
    names = frozenset(tool_names)
    if not names:
        raise fail("INVALID_DEFINITION", "Tool binding needs registered tool names.")
    resolved: set[str] = set()
    for name in names:
        if name in INSTAGRAM_PUBLISHING_TOOLS:
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=str(name),
            )
        definition = registry.get(name)
        if definition.classification == "write" and "write" not in granted_permissions:
            raise fail(
                "UNAUTHORIZED_MUTATION",
                "Mutation tools require an explicit write grant.",
                tool=definition.name,
            )
        if definition.classification == "read" and "read" not in granted_permissions:
            raise fail("TOOL_NOT_ALLOWED", "Tool is not granted to this agent.", tool=definition.name)
        resolved.add(definition.name)
    return ToolBinding(
        agent_id=agent_id.strip(),
        tool_names=frozenset(resolved),
        permissions=granted_permissions,
    )
