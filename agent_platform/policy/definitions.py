"""Declarative policies. Conditions are structured comparisons."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_platform.policy.types import (
    ConditionOperator,
    PolicyCondition,
    PolicyDefinition,
    PolicyDomain,
    PolicyMode,
    PolicyOutcome,
    PolicySubject,
    PolicyVerdict,
    Verdict,
)


def failed_condition_reason(
    definition: PolicyDefinition, facts: Mapping[str, Any]
) -> str | None:
    """Return a deny reason when a declared condition does not hold."""

    for condition in definition.conditions:
        if not _matches(condition, facts):
            if definition.domain is PolicyDomain.ASSET_REQUIREMENT:
                return "asset_requirement_missing"
            return "condition_failed"
    return None


class DeclarativePolicy:
    """Policy built from a ``PolicyDefinition``. Registration is server code."""

    def __init__(self, definition: PolicyDefinition) -> None:
        if type(definition) is not PolicyDefinition:
            raise TypeError("definition must be a PolicyDefinition")
        self._definition = definition

    @property
    def definition(self) -> PolicyDefinition:
        return self._definition

    def evaluate(self, subject: PolicySubject) -> PolicyVerdict:
        failed = failed_condition_reason(self._definition, subject.facts)
        if failed is not None:
            return PolicyVerdict(Verdict.DENY, failed)
        if self._definition.outcome is PolicyOutcome.DENY:
            return PolicyVerdict(Verdict.DENY, "policy_denied")
        if (
            self._definition.mode is PolicyMode.MANUAL
            or self._definition.domain is PolicyDomain.HUMAN_APPROVAL
        ):
            return PolicyVerdict(Verdict.NEEDS_HUMAN, "human_approval_required")
        if self._definition.mode is PolicyMode.AUTO:
            return PolicyVerdict(Verdict.ALLOW, "auto_policy")
        return PolicyVerdict(Verdict.ALLOW, "conditions_met")


def _matches(condition: PolicyCondition, facts: Mapping[str, Any]) -> bool:
    found, value = _lookup(facts, condition.field)
    operator = condition.operator
    if operator is ConditionOperator.EXISTS:
        return found and value is not None
    if operator is ConditionOperator.NOT_EXISTS:
        return (not found) or value is None
    if not found:
        return False
    if operator is ConditionOperator.EQ:
        return _same(value, condition.value)
    if operator is ConditionOperator.NEQ:
        return not _same(value, condition.value)
    if operator is ConditionOperator.IN:
        return any(_same(value, item) for item in condition.value)
    return False


def _lookup(facts: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    current: Any = facts
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return (
            isinstance(left, bool)
            and isinstance(right, bool)
            and bool(left) is bool(right)
        )
    if type(left) is not type(right):
        return False
    return left == right
