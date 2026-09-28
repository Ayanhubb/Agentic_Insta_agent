"""Knowledge sources granted to one agent version. The set may be empty."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from backend.platform.knowledge.catalog import (
    INSTAGRAM_KNOWLEDGE_SOURCES,
    KnowledgeSource,
)
from backend.platform.knowledge.errors import fail

_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class KnowledgeBinding:
    agent_id: str
    version: int
    sources: frozenset[KnowledgeSource]

    def grants(self, source: KnowledgeSource) -> bool:
        return source in self.sources


def bind_knowledge(agent_id: str, version: int, sources: Iterable[str]) -> KnowledgeBinding:
    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        raise fail("INVALID_DEFINITION")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise fail("INVALID_DEFINITION")
    if isinstance(sources, str) or not isinstance(sources, (set, frozenset, list, tuple)):
        raise fail("INVALID_DEFINITION")
    resolved: set[KnowledgeSource] = set()
    for source in sources:
        try:
            resolved.add(source if isinstance(source, KnowledgeSource) else KnowledgeSource(str(source)))
        except ValueError as exc:
            raise fail("INVALID_SOURCE") from exc
    return KnowledgeBinding(agent_id=agent_id, version=version, sources=frozenset(resolved))


def instagram_marketing_knowledge(version: int = 1) -> KnowledgeBinding:
    """Reference agent context. A future agent binds a subset, including none."""
    return KnowledgeBinding(
        agent_id="instagram_marketing",
        version=version,
        sources=INSTAGRAM_KNOWLEDGE_SOURCES,
    )
