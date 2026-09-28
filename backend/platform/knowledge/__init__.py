"""Optional knowledge sources. Bindings name what an agent may load."""

from backend.platform.knowledge.binding import (
    KnowledgeBinding,
    bind_knowledge,
    instagram_marketing_knowledge,
)
from backend.platform.knowledge.catalog import (
    INSTAGRAM_KNOWLEDGE_SOURCES,
    KnowledgeSource,
)
from backend.platform.knowledge.context import McpContextAssembler
from backend.platform.knowledge.errors import KnowledgeAccessError
from backend.platform.knowledge.service import PlatformKnowledgeService
from backend.platform.knowledge.store import KnowledgeStore

__all__ = [
    "INSTAGRAM_KNOWLEDGE_SOURCES",
    "KnowledgeAccessError",
    "KnowledgeBinding",
    "KnowledgeSource",
    "KnowledgeStore",
    "McpContextAssembler",
    "PlatformKnowledgeService",
    "bind_knowledge",
    "instagram_marketing_knowledge",
]
