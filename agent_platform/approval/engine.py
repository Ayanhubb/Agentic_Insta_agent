"""Generic approval engine.

Policy evaluation produces APPROVED, PENDING, or REJECTED. This engine stores
that result and, for PENDING, accepts one human decision. Publishing permission
is recorded only after that human decision, and only for the Instagram agent
on an approved publication workflow with the explicit capability.

This engine does not call the Instagram publisher.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace

from agent_platform.approval.errors import (
    ApprovalNotFound,
    InvalidApprovalState,
    MissingApproval,
    PolicyDenied,
)
from agent_platform.approval.types import ApprovalRecord
from agent_platform.policy.engine import PolicyEngine
from agent_platform.policy.errors import InvalidPolicyRequest, PolicyBypass
from agent_platform.policy.guard import bind_tenant, require_human_actor
from agent_platform.policy.types import (
    DecisionStatus,
    GateAction,
    NextStep,
    PolicyDecision,
    PolicyDomain,
    PolicyRequest,
)


class ApprovalEngine:
    """Stores policy decisions and human approvals for one process."""

    def __init__(self, policies: PolicyEngine) -> None:
        if type(policies) is not PolicyEngine:
            raise PolicyBypass()
        self._policies = policies
        self._lock = threading.Lock()
        self._records: dict[str, ApprovalRecord] = {}

    def submit(self, request: PolicyRequest, tenant: object) -> ApprovalRecord:
        if type(request) is not PolicyRequest:
            raise InvalidPolicyRequest()
        tenant_id, _source = bind_tenant(tenant)
        decision = self._policies.evaluate(request, tenant)
        decision = _without_automatic_publish(decision)
        if decision.tenant_id != tenant_id:
            raise PolicyBypass()
        record = ApprovalRecord(
            id=str(uuid.uuid4()),
            tenant_id=decision.tenant_id,
            policy_id=decision.policy_id,
            mode=decision.mode,
            domain=decision.domain,
            agent_id=decision.agent_id,
            workflow_id=decision.workflow_id,
            status=decision.status,
            reason=decision.reason,
            capability=decision.capability,
        )
        with self._lock:
            self._records[record.id] = record
        return replace(record)

    def get(self, tenant: object, approval_id: str) -> ApprovalRecord:
        tenant_id, _source = bind_tenant(tenant)
        return self._owned(tenant_id, approval_id)

    def decide(
        self,
        tenant: object,
        approval_id: str,
        *,
        actor_id: str,
        approved: bool,
    ) -> ApprovalRecord:
        if type(approved) is not bool:
            raise InvalidPolicyRequest()
        tenant_id, _source = bind_tenant(tenant)
        with self._lock:
            current = self._records.get(approval_id)
            if current is None or current.tenant_id != tenant_id:
                raise ApprovalNotFound()
            require_human_actor(actor_id, current.agent_id)
            if current.status is not DecisionStatus.PENDING:
                raise InvalidApprovalState()
            updated = self._apply(current, actor_id, approved)
            self._records[approval_id] = updated
            return replace(updated)

    def gate(self, tenant: object, approval_id: str) -> NextStep:
        tenant_id, _source = bind_tenant(tenant)
        record = self._owned(tenant_id, approval_id)
        if record.status is DecisionStatus.REJECTED:
            return NextStep(
                action=GateAction.STOP, reason=record.reason, domain=record.domain
            )
        if record.status is DecisionStatus.PENDING:
            return NextStep(
                action=GateAction.WAIT, reason=record.reason, domain=record.domain
            )
        if record.domain is PolicyDomain.PUBLISHING:
            block = self._policies.publishing_block_reason(
                policy_id=record.policy_id,
                agent_id=record.agent_id,
                workflow_id=record.workflow_id,
                capability=record.capability,
            )
            if block is not None:
                return NextStep(
                    action=GateAction.STOP, reason=block, domain=record.domain
                )
            return NextStep(
                action=GateAction.CONTINUE,
                reason=record.reason,
                domain=record.domain,
                publishing_granted=True,
            )
        return NextStep(
            action=GateAction.CONTINUE, reason=record.reason, domain=record.domain
        )

    def require_continue(self, tenant: object, approval_id: str) -> NextStep:
        step = self.gate(tenant, approval_id)
        if step.action is GateAction.WAIT:
            raise MissingApproval()
        if step.action is GateAction.STOP:
            raise PolicyDenied()
        return step

    def _owned(self, tenant_id: str, approval_id: str) -> ApprovalRecord:
        with self._lock:
            record = self._records.get(approval_id)
            if record is None or record.tenant_id != tenant_id:
                raise ApprovalNotFound()
            return replace(record)

    def _apply(
        self, record: ApprovalRecord, actor_id: str, approved: bool
    ) -> ApprovalRecord:
        if approved and record.domain is PolicyDomain.PUBLISHING:
            block = self._policies.publishing_block_reason(
                policy_id=record.policy_id,
                agent_id=record.agent_id,
                workflow_id=record.workflow_id,
                capability=record.capability,
            )
            if block is not None:
                return replace(
                    record,
                    status=DecisionStatus.REJECTED,
                    reason=block,
                    actor_id=actor_id,
                )
        status = DecisionStatus.APPROVED if approved else DecisionStatus.REJECTED
        reason = "human_approved" if approved else "human_rejected"
        return replace(record, status=status, reason=reason, actor_id=actor_id)


def _without_automatic_publish(decision: PolicyDecision) -> PolicyDecision:
    """A publishing evaluation cannot be stored as already approved."""

    if (
        decision.domain is PolicyDomain.PUBLISHING
        and decision.status is DecisionStatus.APPROVED
    ):
        return replace(
            decision, status=DecisionStatus.PENDING, reason="approval_required"
        )
    return decision
