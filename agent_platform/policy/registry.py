"""In-process policy and capability registry.

Policies and capability grants are registered by server code. A request cannot
add either one.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from agent_platform.policy.definitions import DeclarativePolicy
from agent_platform.policy.errors import PolicyBypass, PolicyNotFound, PolicyRejected
from agent_platform.policy.guard import (
    APPROVED_PUBLICATION_WORKFLOWS,
    INSTAGRAM_AGENT_ID,
    PUBLISH_CAPABILITY,
    require_agent_id,
    require_capability_name,
    require_workflow_id,
    scan_document,
)
from agent_platform.policy.interfaces import Policy
from agent_platform.policy.types import (
    ConditionOperator,
    PolicyCondition,
    PolicyDefinition,
    PolicyDomain,
    PolicyMode,
    PolicyOutcome,
)


class PolicyRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._policies: dict[str, Policy] = {}
        self._grants: set[tuple[str, str, str]] = set()

    def register(self, policy: Policy) -> Policy:
        definition = getattr(policy, "definition", None)
        evaluate = getattr(policy, "evaluate", None)
        if (
            type(definition) is not PolicyDefinition
            or not callable(evaluate)
            or isinstance(evaluate, str)
        ):
            raise PolicyRejected("Policy was rejected.")
        with self._lock:
            if definition.id in self._policies:
                raise PolicyRejected("Policy is already registered.")
            self._policies[definition.id] = policy
        return policy

    def register_document(self, document: Mapping[str, Any]) -> DeclarativePolicy:
        policy = DeclarativePolicy(definition_from_document(document))
        self.register(policy)
        return policy

    def get(self, policy_id: str) -> Policy:
        with self._lock:
            policy = self._policies.get(policy_id)
        if policy is None:
            raise PolicyNotFound()
        return policy

    def grant(
        self,
        *,
        agent_id: str,
        capability: str,
        workflows: frozenset[str] | set[str] | list[str] | tuple[str, ...],
    ) -> None:
        agent = require_agent_id(agent_id)
        name = require_capability_name(capability)
        if (
            not isinstance(workflows, (frozenset, set, list, tuple))
            or isinstance(workflows, str)
            or not workflows
        ):
            raise PolicyRejected("Capability grant is not valid.")
        names = frozenset(require_workflow_id(item) for item in workflows)
        if name == PUBLISH_CAPABILITY:
            if agent != INSTAGRAM_AGENT_ID:
                raise PolicyRejected(
                    "Publishing capability is not allowed for this agent."
                )
            if not names <= APPROVED_PUBLICATION_WORKFLOWS:
                raise PolicyRejected(
                    "Publishing capability is not allowed for this workflow."
                )
        with self._lock:
            for workflow_id in names:
                self._grants.add((agent, name, workflow_id))

    def is_granted(
        self, agent_id: str, capability: str | None, workflow_id: str
    ) -> bool:
        if not capability:
            return False
        with self._lock:
            return (agent_id, capability, workflow_id) in self._grants


def definition_from_document(document: Mapping[str, Any]) -> PolicyDefinition:
    if not isinstance(document, Mapping) or isinstance(
        document, (str, bytes, bytearray)
    ):
        raise PolicyBypass()
    scan_document(document)
    try:
        policy_id = document["id"]
        mode = _enum(PolicyMode, document["mode"])
        domain = _enum(PolicyDomain, document["domain"])
        workflows = document["workflows"]
    except KeyError:
        raise PolicyRejected("Policy definition is not valid.") from None
    if not isinstance(workflows, list) or isinstance(workflows, str):
        raise PolicyRejected("Policy definition is not valid.")
    outcome = _enum(PolicyOutcome, document.get("outcome", "allow"))
    capability = document.get("capability")
    agent_id = document.get("agent_id")
    if capability is not None and not isinstance(capability, str):
        raise PolicyRejected("Policy definition is not valid.")
    if agent_id is not None and not isinstance(agent_id, str):
        raise PolicyRejected("Policy definition is not valid.")
    return PolicyDefinition(
        id=policy_id,
        mode=mode,
        domain=domain,
        workflows=frozenset(workflows),
        outcome=outcome,
        conditions=_conditions(document.get("conditions", [])),
        capability=capability,
        agent_id=agent_id,
    )


def _conditions(raw: object) -> tuple[PolicyCondition, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PolicyRejected("Policy condition is not valid.")
    built: list[PolicyCondition] = []
    for item in raw:
        if not isinstance(item, Mapping) or isinstance(item, (str, bytes, bytearray)):
            raise PolicyRejected("Policy condition is not valid.")
        try:
            operator = _enum(ConditionOperator, item["operator"])
            field = item["field"]
        except KeyError:
            raise PolicyRejected("Policy condition is not valid.") from None
        built.append(
            PolicyCondition(field=field, operator=operator, value=item.get("value"))
        )
    return tuple(built)


def _enum(kind: type[Any], value: object) -> Any:
    if isinstance(value, kind):
        return value
    try:
        return kind(value)
    except (ValueError, TypeError):
        raise PolicyRejected("Policy definition is not valid.") from None
