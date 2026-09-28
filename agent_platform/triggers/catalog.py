"""In-memory trigger definitions. Rows are scoped by tenant."""

from __future__ import annotations

from agent_platform.triggers.types import TriggerDefinition


class TriggerCatalog:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], TriggerDefinition] = {}

    def save(self, definition: TriggerDefinition) -> None:
        self._items[(definition.tenant_id, definition.trigger_id)] = definition

    def get(self, tenant_id: str, trigger_id: str) -> TriggerDefinition | None:
        return self._items.get((tenant_id, trigger_id))

    def list(self, tenant_id: str | None = None) -> list[TriggerDefinition]:
        rows = list(self._items.values())
        if tenant_id is None:
            return rows
        return [row for row in rows if row.tenant_id == tenant_id]
