"""In-process capability registry.

Handlers are callables registered by server code. A workflow step stores the
capability name only. The engine never imports that name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_platform.workflow.errors import CapabilityRejected, InvalidDefinition, UnknownCapability
from agent_platform.workflow.guard import require_capability_name
from agent_platform.workflow.types import CAPABILITY_STEP_TYPES, StepType

# Same names MCP refuses. This engine is not a second publisher, so it will not
# register or resolve them. The set is duplicated here so the workflow package
# does not import the Instagram agent.
BLOCKED_CAPABILITY_NAMES = frozenset(
    {
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
        "media_publish",
        "instagram_publish",
        "publish",
        "post_to_instagram",
        "create_media",
        "publish_media",
        "eval",
        "exec",
        "compile",
        "os.system",
        "subprocess.run",
        "subprocess.call",
        "subprocess.popen",
    }
)

CapabilityHandler = Callable[["CapabilityCall"], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class CapabilityCall:
    """Arguments passed to a registered handler. There is no secret field."""

    tenant_id: str
    tenant_source: str
    execution_id: str
    workflow_id: str
    step_id: str
    capability: str
    data: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    name: str
    step_types: frozenset[StepType]
    handler: CapabilityHandler


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredCapability] = {}

    def register(
        self,
        name: str,
        handler: CapabilityHandler,
        *,
        step_types: frozenset[StepType] | set[StepType],
    ) -> CapabilityRegistry:
        try:
            require_capability_name(name)
        except InvalidDefinition:
            raise CapabilityRejected() from None
        if name in BLOCKED_CAPABILITY_NAMES or name.split(".", 1)[0] in {"os", "sys", "subprocess", "importlib", "builtins"}:
            raise CapabilityRejected()
        allowed = frozenset(step_types)
        if not allowed or not allowed <= CAPABILITY_STEP_TYPES:
            raise CapabilityRejected("Capability step types are not valid.")
        if not callable(handler) or isinstance(handler, str):
            raise CapabilityRejected("A capability handler must be a registered callable.")
        if name in self._items:
            raise CapabilityRejected("Capability is already registered.")
        self._items[name] = RegisteredCapability(name=name, step_types=allowed, handler=handler)
        return self

    def resolve(self, name: str, step_type: StepType) -> RegisteredCapability:
        if not isinstance(name, str) or name in BLOCKED_CAPABILITY_NAMES or name not in self._items:
            raise UnknownCapability()
        item = self._items[name]
        if step_type not in item.step_types:
            raise CapabilityRejected("Capability is not registered for this step type.")
        return item

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items
