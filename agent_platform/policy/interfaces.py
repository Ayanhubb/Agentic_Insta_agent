"""Reusable policy interfaces.

Server code registers objects that satisfy ``Policy``. ``PolicyEvaluator``
is the engine that turns those opinions into APPROVED, PENDING, or REJECTED.
A policy implementation does not grant publishing.
"""

from __future__ import annotations

from typing import Protocol

from agent_platform.policy.types import (
    NextStep,
    PolicyDecision,
    PolicyDefinition,
    PolicyRequest,
    PolicySubject,
    PolicyVerdict,
)


class Policy(Protocol):
    """One named rule. ``evaluate`` returns an opinion, not a final status."""

    @property
    def definition(self) -> PolicyDefinition: ...

    def evaluate(self, subject: PolicySubject) -> PolicyVerdict: ...


class PolicyEvaluator(Protocol):
    """Evaluates a request for a trusted tenant and reports the next step."""

    def evaluate(self, request: PolicyRequest, tenant: object) -> PolicyDecision: ...

    def next_action(self, decision: PolicyDecision) -> NextStep: ...
