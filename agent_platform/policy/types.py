"""Structured policy records.

A definition is data registered by server code. It does not contain a callable,
a module path, or a flag that grants publishing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from agent_platform.policy.errors import PolicyRejected
from agent_platform.policy.guard import (
    APPROVED_PUBLICATION_WORKFLOWS,
    INSTAGRAM_AGENT_ID,
    PUBLISH_CAPABILITY,
    require_agent_id,
    require_capability_name,
    require_field_path,
    require_policy_id,
    require_workflow_id,
)


class PolicyMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    CONDITIONAL = "conditional"


class PolicyDomain(str, Enum):
    TOOL_ACCESS = "tool_access"
    DATA_ACCESS = "data_access"
    EXTERNAL_ACTION = "external_action"
    PUBLISHING = "publishing"
    FINANCIAL_ACTION = "financial_action"
    CONTENT_APPROVAL = "content_approval"
    ASSET_REQUIREMENT = "asset_requirement"
    HUMAN_APPROVAL = "human_approval"


class PolicyOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class EffectClass(str, Enum):
    NONE = "none"
    EXTERNAL = "external"
    PUBLISHING = "publishing"
    FINANCIAL = "financial"


class DecisionStatus(str, Enum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN = "needs_human"


class ConditionOperator(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    IN = "in"


class GateAction(str, Enum):
    CONTINUE = "continue"
    WAIT = "wait"
    STOP = "stop"


_CAPABILITY_DOMAINS = frozenset(
    {
        PolicyDomain.EXTERNAL_ACTION,
        PolicyDomain.FINANCIAL_ACTION,
        PolicyDomain.PUBLISHING,
    }
)
_DOMAIN_EFFECT = {
    PolicyDomain.TOOL_ACCESS: EffectClass.NONE,
    PolicyDomain.DATA_ACCESS: EffectClass.NONE,
    PolicyDomain.EXTERNAL_ACTION: EffectClass.EXTERNAL,
    PolicyDomain.PUBLISHING: EffectClass.PUBLISHING,
    PolicyDomain.FINANCIAL_ACTION: EffectClass.FINANCIAL,
    PolicyDomain.CONTENT_APPROVAL: EffectClass.NONE,
    PolicyDomain.ASSET_REQUIREMENT: EffectClass.NONE,
    PolicyDomain.HUMAN_APPROVAL: EffectClass.NONE,
}


def effect_for(domain: PolicyDomain) -> EffectClass:
    return _DOMAIN_EFFECT[domain]


def _scalar(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= 4_096
    return False


@dataclass(frozen=True, slots=True)
class PolicyCondition:
    """Comparison against sanitized facts. This is data, not an expression."""

    field: str
    operator: ConditionOperator
    value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", require_field_path(self.field))
        if not isinstance(self.operator, ConditionOperator):
            raise PolicyRejected("Policy condition is not valid.")
        if self.operator in {ConditionOperator.EXISTS, ConditionOperator.NOT_EXISTS}:
            if self.value is not None:
                raise PolicyRejected("Policy condition is not valid.")
            return
        if self.operator is ConditionOperator.IN:
            raw = self.value
            if isinstance(raw, list):
                raw = tuple(raw)
                object.__setattr__(self, "value", raw)
            if (
                not isinstance(raw, tuple)
                or not raw
                or len(raw) > 32
                or not all(_scalar(item) for item in raw)
            ):
                raise PolicyRejected("Policy condition is not valid.")
            return
        if not _scalar(self.value):
            raise PolicyRejected("Policy condition is not valid.")


@dataclass(frozen=True, slots=True)
class PolicyDefinition:
    """Named rule. Publishing definitions cannot use automatic mode."""

    id: str
    mode: PolicyMode
    domain: PolicyDomain
    workflows: frozenset[str]
    outcome: PolicyOutcome = PolicyOutcome.ALLOW
    conditions: tuple[PolicyCondition, ...] = ()
    capability: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_policy_id(self.id))
        if not isinstance(self.mode, PolicyMode) or not isinstance(
            self.domain, PolicyDomain
        ):
            raise PolicyRejected("Policy definition is not valid.")
        if not isinstance(self.outcome, PolicyOutcome):
            raise PolicyRejected("Policy definition is not valid.")
        workflows = self.workflows
        if isinstance(workflows, (list, tuple, set)):
            workflows = frozenset(workflows)
        if not isinstance(workflows, frozenset) or not workflows or len(workflows) > 8:
            raise PolicyRejected("Policy definition is not valid.")
        object.__setattr__(
            self,
            "workflows",
            frozenset(require_workflow_id(item) for item in workflows),
        )
        if self.agent_id is not None:
            object.__setattr__(self, "agent_id", require_agent_id(self.agent_id))
        if len(self.conditions) > 32 or not all(
            isinstance(item, PolicyCondition) for item in self.conditions
        ):
            raise PolicyRejected("Policy condition is not valid.")
        if self.capability is not None:
            object.__setattr__(
                self, "capability", require_capability_name(self.capability)
            )
        if self.domain is PolicyDomain.PUBLISHING and self.mode is PolicyMode.AUTO:
            raise PolicyRejected("Publishing cannot be automatic.")
        if (
            self.domain is PolicyDomain.HUMAN_APPROVAL
            and self.mode is not PolicyMode.MANUAL
        ):
            raise PolicyRejected("Human approval must stay manual.")
        if (
            self.domain is not PolicyDomain.PUBLISHING
            and self.capability == PUBLISH_CAPABILITY
        ):
            raise PolicyRejected(
                "Publishing capability is only valid for publishing policy."
            )
        if (
            self.outcome is PolicyOutcome.ALLOW
            and self.domain in _CAPABILITY_DOMAINS
            and self.capability is None
        ):
            raise PolicyRejected("This policy requires an explicit capability.")
        if (
            self.domain is PolicyDomain.PUBLISHING
            and self.outcome is PolicyOutcome.ALLOW
        ):
            if self.agent_id != INSTAGRAM_AGENT_ID:
                raise PolicyRejected(
                    "Only the Instagram agent can bind a publishing policy."
                )
            if self.capability != PUBLISH_CAPABILITY:
                raise PolicyRejected(
                    "Publishing requires the instagram.publish capability."
                )
            if not self.workflows <= APPROVED_PUBLICATION_WORKFLOWS:
                raise PolicyRejected(
                    "Publishing policy workflows are not approved publication workflows."
                )


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """Opinion of a registered policy. The engine decides the status."""

    verdict: Verdict
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, Verdict):
            raise PolicyRejected("Policy result is not valid.")
        if (
            not isinstance(self.reason, str)
            or not self.reason.replace("_", "").isalnum()
        ):
            raise PolicyRejected("Policy result is not valid.")


@dataclass(frozen=True, slots=True)
class PolicySubject:
    """Sanitized view passed to a policy implementation."""

    tenant_id: str
    tenant_source: str
    agent_id: str
    workflow_id: str
    domain: PolicyDomain
    effect: EffectClass
    capability: str | None
    facts: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Caller input. Tenant is a separate trusted argument, not a field here."""

    policy_id: str
    agent_id: str
    workflow_id: str
    domain: PolicyDomain
    effect: EffectClass
    capability: str | None = None
    facts: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Engine result. Publishing decisions from this object are pending or rejected."""

    status: DecisionStatus
    reason: str
    policy_id: str
    mode: PolicyMode
    domain: PolicyDomain
    agent_id: str
    workflow_id: str
    tenant_id: str
    capability: str | None = None


@dataclass(frozen=True, slots=True)
class NextStep:
    """What the caller may do after evaluation.

    ``executes_effect`` stays false. This layer does not run the effect.
    ``publishing_granted`` is set only by the approval engine after a human
    approval of an eligible Instagram publication request.
    """

    action: GateAction
    reason: str
    domain: PolicyDomain
    publishing_granted: bool = False
    executes_effect: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "executes_effect", False)
        granted = bool(
            self.publishing_granted
            and self.action is GateAction.CONTINUE
            and self.domain is PolicyDomain.PUBLISHING
        )
        object.__setattr__(self, "publishing_granted", granted)


def freeze_facts(facts: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(facts))
