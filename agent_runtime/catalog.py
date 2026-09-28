"""In-memory catalog. Definitions stay in process. This is not a database."""

from __future__ import annotations

from typing import Protocol

from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    AgentDefinition,
    AgentVersion,
    OwnerKind,
    VersionStatus,
    WorkflowVersion,
)


def scope_key(definition: AgentDefinition) -> str:
    if definition.owner == OwnerKind.SYSTEM:
        return f"system:{definition.key}"
    return f"user:{definition.user_id}:{definition.key}"


class AgentCatalog(Protocol):
    def get_definition(self, agent_key: str, *, tenant_id: str) -> AgentDefinition | None: ...

    def get_version(self, definition: AgentDefinition, version_number: int) -> AgentVersion | None: ...

    def get_draft(self, definition: AgentDefinition) -> AgentVersion | None: ...

    def get_workflow(self, workflow_key: str, version_number: int) -> WorkflowVersion | None: ...


class InMemoryCatalog:
    """Process catalog. Published versions cannot be edited in place."""

    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._versions: dict[tuple[str, int], AgentVersion] = {}
        self._workflows: dict[tuple[str, int], WorkflowVersion] = {}

    def save_definition(self, definition: AgentDefinition) -> None:
        if definition.published_version_number is not None:
            version = self._versions.get((scope_key(definition), definition.published_version_number))
            if version is None or version.status != VersionStatus.PUBLISHED:
                raise RuntimeFailure(
                    RuntimeErrorCode.VERSION_NOT_PUBLISHED,
                    "The published pointer must reference a published version.",
                )
        self._definitions[scope_key(definition)] = definition

    def save_version(self, definition: AgentDefinition, version: AgentVersion) -> None:
        scope = scope_key(definition)
        if scope not in self._definitions:
            raise RuntimeFailure(RuntimeErrorCode.AGENT_NOT_FOUND, "Agent was not found.")
        slot = (scope, version.version_number)
        existing = self._versions.get(slot)
        if existing is not None and existing.status == VersionStatus.PUBLISHED:
            raise RuntimeFailure(
                RuntimeErrorCode.INVALID_REQUEST,
                "A published agent version is immutable.",
            )
        if version.status == VersionStatus.DRAFT:
            for (stored_scope, number), stored in self._versions.items():
                if (
                    stored_scope == scope
                    and stored.status == VersionStatus.DRAFT
                    and number != version.version_number
                ):
                    raise RuntimeFailure(
                        RuntimeErrorCode.INVALID_REQUEST,
                        "An agent can have only one draft version.",
                    )
        self._versions[slot] = version

    def save_workflow(self, workflow: WorkflowVersion) -> None:
        slot = (workflow.workflow_key, workflow.version_number)
        existing = self._workflows.get(slot)
        if existing is not None and existing.status == VersionStatus.PUBLISHED:
            raise RuntimeFailure(
                RuntimeErrorCode.INVALID_REQUEST,
                "A published workflow version is immutable.",
            )
        self._workflows[slot] = workflow

    def get_definition(self, agent_key: str, *, tenant_id: str) -> AgentDefinition | None:
        user_hit = self._definitions.get(f"user:{tenant_id}:{agent_key}")
        if user_hit is not None:
            return user_hit
        return self._definitions.get(f"system:{agent_key}")

    def get_version(self, definition: AgentDefinition, version_number: int) -> AgentVersion | None:
        return self._versions.get((scope_key(definition), version_number))

    def get_draft(self, definition: AgentDefinition) -> AgentVersion | None:
        scope = scope_key(definition)
        drafts = [
            version
            for (stored_scope, _), version in self._versions.items()
            if stored_scope == scope and version.status == VersionStatus.DRAFT
        ]
        if not drafts:
            return None
        return drafts[0]

    def get_workflow(self, workflow_key: str, version_number: int) -> WorkflowVersion | None:
        return self._workflows.get((workflow_key, version_number))
