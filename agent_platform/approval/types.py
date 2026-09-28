"""Approval records. A record is the human decision, not an effect execution."""

from __future__ import annotations

from dataclasses import dataclass

from agent_platform.policy.types import DecisionStatus, PolicyDomain, PolicyMode


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    tenant_id: str
    policy_id: str
    mode: PolicyMode
    domain: PolicyDomain
    agent_id: str
    workflow_id: str
    status: DecisionStatus
    reason: str
    capability: str | None = None
    actor_id: str | None = None
