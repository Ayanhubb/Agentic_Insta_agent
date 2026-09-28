"""Generic policy engine.

Agent → workflow → this evaluation → APPROVED, PENDING, or REJECTED.

Publishing is never approved here. An eligible Instagram publication stays
PENDING until the approval engine records a human decision. External and
financial effects require a capability grant registered in server code.
"""

from __future__ import annotations

from agent_platform.policy.definitions import failed_condition_reason
from agent_platform.policy.errors import (
    InvalidPolicyRequest,
    InvalidPolicyResult,
    PolicyMismatch,
    PolicyNotFound,
)
from agent_platform.policy.guard import (
    APPROVED_PUBLICATION_WORKFLOWS,
    BLOCKED_CAPABILITY_NAMES,
    INSTAGRAM_AGENT_ID,
    PUBLISH_CAPABILITY,
    bind_tenant,
    optional_request_capability,
    require_request_id,
    sanitize_facts,
)
from agent_platform.policy.registry import PolicyRegistry
from agent_platform.policy.types import (
    DecisionStatus,
    EffectClass,
    GateAction,
    NextStep,
    PolicyDecision,
    PolicyDefinition,
    PolicyDomain,
    PolicyMode,
    PolicyOutcome,
    PolicyRequest,
    PolicySubject,
    PolicyVerdict,
    Verdict,
    effect_for,
    freeze_facts,
)

_CAPABILITY_DOMAINS = frozenset(
    {PolicyDomain.EXTERNAL_ACTION, PolicyDomain.FINANCIAL_ACTION}
)
_DENY_REASONS = frozenset(
    {"policy_denied", "condition_failed", "asset_requirement_missing"}
)


class PolicyEngine:
    """Evaluates registered policies for one trusted tenant."""

    def __init__(self, registry: PolicyRegistry) -> None:
        self._registry = registry

    def evaluate(self, request: PolicyRequest, tenant: object) -> PolicyDecision:
        if type(request) is not PolicyRequest:
            raise InvalidPolicyRequest()
        tenant_id, source = bind_tenant(tenant)
        policy_id = require_request_id(request.policy_id, kind="policy")
        agent_id = require_request_id(request.agent_id, kind="agent")
        workflow_id = require_request_id(request.workflow_id, kind="workflow")
        if not isinstance(request.domain, PolicyDomain) or not isinstance(
            request.effect, EffectClass
        ):
            raise InvalidPolicyRequest()
        if request.effect is not effect_for(request.domain):
            raise PolicyMismatch()
        capability = optional_request_capability(request.capability)
        facts = sanitize_facts(request.facts)
        policy = self._registry.get(policy_id)
        definition = policy.definition
        if (
            type(definition) is not PolicyDefinition
            or definition.domain is not request.domain
        ):
            raise PolicyMismatch()
        subject = PolicySubject(
            tenant_id=tenant_id,
            tenant_source=source,
            agent_id=agent_id,
            workflow_id=workflow_id,
            domain=request.domain,
            effect=request.effect,
            capability=capability,
            facts=freeze_facts(facts),
        )
        verdict = policy.evaluate(subject)
        if type(verdict) is not PolicyVerdict:
            raise InvalidPolicyResult()
        return self._finalize(definition, subject, facts, verdict)

    def next_action(self, decision: PolicyDecision) -> NextStep:
        """Map a decision to the next step. Publishing is not continued from here."""

        if type(decision) is not PolicyDecision:
            raise InvalidPolicyRequest()
        if decision.domain is PolicyDomain.PUBLISHING:
            if decision.status is DecisionStatus.REJECTED:
                return NextStep(
                    action=GateAction.STOP,
                    reason=decision.reason,
                    domain=decision.domain,
                )
            return NextStep(
                action=GateAction.WAIT,
                reason="approval_required",
                domain=decision.domain,
            )
        if decision.status is DecisionStatus.APPROVED:
            return NextStep(
                action=GateAction.CONTINUE,
                reason=decision.reason,
                domain=decision.domain,
            )
        if decision.status is DecisionStatus.PENDING:
            return NextStep(
                action=GateAction.WAIT, reason=decision.reason, domain=decision.domain
            )
        return NextStep(
            action=GateAction.STOP, reason=decision.reason, domain=decision.domain
        )

    def publishing_block_reason(
        self,
        *,
        policy_id: str,
        agent_id: str,
        workflow_id: str,
        capability: str | None,
    ) -> str | None:
        """Return a deny reason when a publishing approval must not be granted."""

        try:
            definition = self._registry.get(policy_id).definition
        except PolicyNotFound:
            return "unauthorized_publisher"
        if (
            type(definition) is not PolicyDefinition
            or definition.domain is not PolicyDomain.PUBLISHING
        ):
            return "unauthorized_publisher"
        if definition.mode is PolicyMode.AUTO:
            return "publishing_not_automatic"
        if definition.outcome is PolicyOutcome.DENY:
            return "policy_denied"
        if agent_id != INSTAGRAM_AGENT_ID or definition.agent_id != INSTAGRAM_AGENT_ID:
            return "unauthorized_publisher"
        if (
            workflow_id not in APPROVED_PUBLICATION_WORKFLOWS
            or workflow_id not in definition.workflows
        ):
            return "unauthorized_workflow"
        if (
            capability != PUBLISH_CAPABILITY
            or definition.capability != PUBLISH_CAPABILITY
            or capability in BLOCKED_CAPABILITY_NAMES
            or not self._registry.is_granted(agent_id, capability, workflow_id)
        ):
            return "unauthorized_side_effect"
        return None

    def _finalize(
        self,
        definition: PolicyDefinition,
        subject: PolicySubject,
        facts: dict[str, object],
        verdict: PolicyVerdict,
    ) -> PolicyDecision:
        if definition.domain is PolicyDomain.PUBLISHING:
            return self._finalize_publishing(definition, subject, facts, verdict)
        if definition.agent_id is not None and subject.agent_id != definition.agent_id:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "agent_not_allowed"
            )
        if subject.workflow_id not in definition.workflows:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "unauthorized_workflow"
            )
        failed = failed_condition_reason(definition, facts)
        if failed is not None:
            return self._decision(definition, subject, DecisionStatus.REJECTED, failed)
        if definition.outcome is PolicyOutcome.DENY or verdict.verdict is Verdict.DENY:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, _deny_reason(verdict)
            )
        if definition.domain in _CAPABILITY_DOMAINS and not self._capability_ok(
            definition, subject
        ):
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "unauthorized_side_effect"
            )
        if (
            definition.mode is PolicyMode.MANUAL
            or verdict.verdict is Verdict.NEEDS_HUMAN
        ):
            return self._decision(
                definition, subject, DecisionStatus.PENDING, "human_approval_required"
            )
        if definition.mode is PolicyMode.AUTO and verdict.verdict is Verdict.ALLOW:
            return self._decision(
                definition, subject, DecisionStatus.APPROVED, "auto_policy"
            )
        if (
            definition.mode is PolicyMode.CONDITIONAL
            and verdict.verdict is Verdict.ALLOW
        ):
            return self._decision(
                definition, subject, DecisionStatus.APPROVED, "conditions_met"
            )
        return self._decision(
            definition, subject, DecisionStatus.REJECTED, "policy_denied"
        )

    def _finalize_publishing(
        self,
        definition: PolicyDefinition,
        subject: PolicySubject,
        facts: dict[str, object],
        verdict: PolicyVerdict,
    ) -> PolicyDecision:
        if definition.mode is PolicyMode.AUTO:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "publishing_not_automatic"
            )
        if subject.workflow_id not in definition.workflows:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "unauthorized_workflow"
            )
        failed = failed_condition_reason(definition, facts)
        if failed is not None:
            return self._decision(definition, subject, DecisionStatus.REJECTED, failed)
        if definition.outcome is PolicyOutcome.DENY or verdict.verdict is Verdict.DENY:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, _deny_reason(verdict)
            )
        if (
            subject.agent_id != INSTAGRAM_AGENT_ID
            or definition.agent_id != INSTAGRAM_AGENT_ID
        ):
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "unauthorized_publisher"
            )
        if subject.workflow_id not in APPROVED_PUBLICATION_WORKFLOWS:
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "unauthorized_workflow"
            )
        if subject.capability != PUBLISH_CAPABILITY or not self._registry.is_granted(
            subject.agent_id, subject.capability, subject.workflow_id
        ):
            return self._decision(
                definition, subject, DecisionStatus.REJECTED, "unauthorized_side_effect"
            )
        return self._decision(
            definition, subject, DecisionStatus.PENDING, "approval_required"
        )

    def _capability_ok(
        self, definition: PolicyDefinition, subject: PolicySubject
    ) -> bool:
        capability = subject.capability
        if (
            capability is None
            or capability != definition.capability
            or capability in BLOCKED_CAPABILITY_NAMES
            or capability == PUBLISH_CAPABILITY
        ):
            return False
        return self._registry.is_granted(
            subject.agent_id, capability, subject.workflow_id
        )

    def _decision(
        self,
        definition: PolicyDefinition,
        subject: PolicySubject,
        status: DecisionStatus,
        reason: str,
    ) -> PolicyDecision:
        if (
            definition.domain is PolicyDomain.PUBLISHING
            and status is DecisionStatus.APPROVED
        ):
            status = DecisionStatus.PENDING
            reason = "approval_required"
        return PolicyDecision(
            status=status,
            reason=reason,
            policy_id=definition.id,
            mode=definition.mode,
            domain=definition.domain,
            agent_id=subject.agent_id,
            workflow_id=subject.workflow_id,
            tenant_id=subject.tenant_id,
            capability=subject.capability,
        )


def _deny_reason(verdict: PolicyVerdict) -> str:
    if verdict.verdict is Verdict.DENY and verdict.reason in _DENY_REASONS:
        return verdict.reason
    return "policy_denied"
