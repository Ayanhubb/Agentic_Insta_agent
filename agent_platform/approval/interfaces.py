"""Reusable approval interface.

``submit`` stores the policy decision. ``decide`` records a human. ``gate``
reports whether the next step may continue.
"""

from __future__ import annotations

from typing import Protocol

from agent_platform.approval.types import ApprovalRecord
from agent_platform.policy.types import NextStep, PolicyRequest


class ApprovalGate(Protocol):
    def submit(self, request: PolicyRequest, tenant: object) -> ApprovalRecord: ...

    def decide(
        self,
        tenant: object,
        approval_id: str,
        *,
        actor_id: str,
        approved: bool,
    ) -> ApprovalRecord: ...

    def gate(self, tenant: object, approval_id: str) -> NextStep: ...

    def require_continue(self, tenant: object, approval_id: str) -> NextStep: ...
