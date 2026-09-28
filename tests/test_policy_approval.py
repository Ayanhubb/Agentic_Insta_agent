"""Generic policy and approval engines. Instagram publishing is not invoked."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_platform.approval import (
    ApprovalEngine,
    ApprovalNotFound,
    InvalidApprovalState,
    MissingApproval,
    PolicyDenied,
)
from agent_platform.policy import (
    APPROVED_PUBLICATION_WORKFLOWS,
    BLOCKED_CAPABILITY_NAMES,
    INSTAGRAM_AGENT_ID,
    PUBLISH_CAPABILITY,
    ConditionOperator,
    DecisionStatus,
    DeclarativePolicy,
    EffectClass,
    GateAction,
    InvalidPolicyRequest,
    InvalidPolicyResult,
    PolicyBypass,
    PolicyCondition,
    PolicyDecision,
    PolicyDefinition,
    PolicyDomain,
    PolicyEngine,
    PolicyMode,
    PolicyOutcome,
    PolicyRegistry,
    PolicyRejected,
    PolicyRequest,
    PolicyVerdict,
    SecretRejected,
    Verdict,
)
from agent_platform.workflow.registry import (
    BLOCKED_CAPABILITY_NAMES as WORKFLOW_BLOCKED,
)
from backend.mcp.tenant_isolation import trusted_tenant

ROOTS = (
    Path(__file__).resolve().parents[1] / "agent_platform" / "policy",
    Path(__file__).resolve().parents[1] / "agent_platform" / "approval",
)


def _stack() -> tuple[PolicyRegistry, PolicyEngine, ApprovalEngine]:
    registry = PolicyRegistry()
    policies = PolicyEngine(registry)
    return registry, policies, ApprovalEngine(policies)


def _request(
    policy_id: str,
    *,
    agent: str,
    workflow: str,
    domain: PolicyDomain,
    effect: EffectClass,
    capability: str | None = None,
    facts: dict | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        policy_id=policy_id,
        agent_id=agent,
        workflow_id=workflow,
        domain=domain,
        effect=effect,
        capability=capability,
        facts=facts,
    )


def test_manual_approval_waits_until_a_human_decides() -> None:
    registry, policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="content.review",
                mode=PolicyMode.MANUAL,
                domain=PolicyDomain.CONTENT_APPROVAL,
                workflows=frozenset({"catalog.review"}),
                agent_id="catalog_agent",
            )
        )
    )
    request = _request(
        "content.review",
        agent="catalog_agent",
        workflow="catalog.review",
        domain=PolicyDomain.CONTENT_APPROVAL,
        effect=EffectClass.NONE,
    )

    decision = policies.evaluate(request, trusted_tenant("tenant-a"))
    step = policies.next_action(decision)
    record = approvals.submit(request, trusted_tenant("tenant-a"))

    assert decision.status is DecisionStatus.PENDING
    assert decision.reason == "human_approval_required"
    assert step.action is GateAction.WAIT
    assert step.publishing_granted is False
    assert step.executes_effect is False
    assert record.status is DecisionStatus.PENDING
    with pytest.raises(MissingApproval):
        approvals.require_continue(trusted_tenant("tenant-a"), record.id)

    approved = approvals.decide(
        trusted_tenant("tenant-a"), record.id, actor_id="reviewer-1", approved=True
    )
    continued = approvals.require_continue(trusted_tenant("tenant-a"), record.id)

    assert approved.status is DecisionStatus.APPROVED
    assert approved.reason == "human_approved"
    assert approved.actor_id == "reviewer-1"
    assert continued.action is GateAction.CONTINUE
    assert continued.publishing_granted is False
    assert continued.executes_effect is False


def test_auto_approval_continues_without_a_human() -> None:
    registry, policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="tool.read",
                mode=PolicyMode.AUTO,
                domain=PolicyDomain.TOOL_ACCESS,
                workflows=frozenset({"catalog.read"}),
            )
        )
    )
    request = _request(
        "tool.read",
        agent="catalog_agent",
        workflow="catalog.read",
        domain=PolicyDomain.TOOL_ACCESS,
        effect=EffectClass.NONE,
    )

    decision = policies.evaluate(request, trusted_tenant("tenant-a"))
    step = policies.next_action(decision)
    record = approvals.submit(request, trusted_tenant("tenant-a"))
    continued = approvals.require_continue(trusted_tenant("tenant-a"), record.id)

    assert decision.status is DecisionStatus.APPROVED
    assert decision.reason == "auto_policy"
    assert decision.mode is PolicyMode.AUTO
    assert step.action is GateAction.CONTINUE
    assert step.publishing_granted is False
    assert step.executes_effect is False
    assert record.status is DecisionStatus.APPROVED
    assert continued.action is GateAction.CONTINUE
    assert continued.executes_effect is False


def test_conditional_policy_follows_declared_facts() -> None:
    registry, policies, _approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="content.qa",
                mode=PolicyMode.CONDITIONAL,
                domain=PolicyDomain.CONTENT_APPROVAL,
                workflows=frozenset({"catalog.review"}),
                conditions=(
                    PolicyCondition(
                        field="qa_passed", operator=ConditionOperator.EQ, value=True
                    ),
                ),
            )
        )
    )
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="asset.logo",
                mode=PolicyMode.CONDITIONAL,
                domain=PolicyDomain.ASSET_REQUIREMENT,
                workflows=frozenset({"catalog.review"}),
                conditions=(
                    PolicyCondition(
                        field="logo_asset_id", operator=ConditionOperator.EXISTS
                    ),
                ),
            )
        )
    )

    failed = policies.evaluate(
        _request(
            "content.qa",
            agent="catalog_agent",
            workflow="catalog.review",
            domain=PolicyDomain.CONTENT_APPROVAL,
            effect=EffectClass.NONE,
            facts={"qa_passed": False},
        ),
        trusted_tenant("tenant-a"),
    )
    passed = policies.evaluate(
        _request(
            "content.qa",
            agent="catalog_agent",
            workflow="catalog.review",
            domain=PolicyDomain.CONTENT_APPROVAL,
            effect=EffectClass.NONE,
            facts={"qa_passed": True},
        ),
        trusted_tenant("tenant-a"),
    )
    missing_logo = policies.evaluate(
        _request(
            "asset.logo",
            agent="catalog_agent",
            workflow="catalog.review",
            domain=PolicyDomain.ASSET_REQUIREMENT,
            effect=EffectClass.NONE,
            facts={},
        ),
        trusted_tenant("tenant-a"),
    )

    assert failed.status is DecisionStatus.REJECTED
    assert failed.reason == "condition_failed"
    assert policies.next_action(failed).action is GateAction.STOP
    assert passed.status is DecisionStatus.APPROVED
    assert passed.reason == "conditions_met"
    assert missing_logo.status is DecisionStatus.REJECTED
    assert missing_logo.reason == "asset_requirement_missing"


def test_denied_policy_stops_the_next_step() -> None:
    registry, policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="data.closed",
                mode=PolicyMode.AUTO,
                domain=PolicyDomain.DATA_ACCESS,
                workflows=frozenset({"catalog.read"}),
                outcome=PolicyOutcome.DENY,
            )
        )
    )
    request = _request(
        "data.closed",
        agent="catalog_agent",
        workflow="catalog.read",
        domain=PolicyDomain.DATA_ACCESS,
        effect=EffectClass.NONE,
    )

    decision = policies.evaluate(request, trusted_tenant("tenant-a"))
    record = approvals.submit(request, trusted_tenant("tenant-a"))

    assert decision.status is DecisionStatus.REJECTED
    assert decision.reason == "policy_denied"
    assert policies.next_action(decision).action is GateAction.STOP
    assert (
        approvals.gate(trusted_tenant("tenant-a"), record.id).action is GateAction.STOP
    )
    with pytest.raises(PolicyDenied):
        approvals.require_continue(trusted_tenant("tenant-a"), record.id)
    with pytest.raises(InvalidApprovalState):
        approvals.decide(
            trusted_tenant("tenant-a"), record.id, actor_id="reviewer-1", approved=True
        )


def test_missing_approval_blocks_the_next_step() -> None:
    registry, _policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="human.gate",
                mode=PolicyMode.MANUAL,
                domain=PolicyDomain.HUMAN_APPROVAL,
                workflows=frozenset({"catalog.review"}),
            )
        )
    )
    request = _request(
        "human.gate",
        agent="catalog_agent",
        workflow="catalog.review",
        domain=PolicyDomain.HUMAN_APPROVAL,
        effect=EffectClass.NONE,
    )
    record = approvals.submit(request, trusted_tenant("tenant-a"))

    assert record.status is DecisionStatus.PENDING
    assert (
        approvals.gate(trusted_tenant("tenant-a"), record.id).action is GateAction.WAIT
    )
    with pytest.raises(MissingApproval):
        approvals.require_continue(trusted_tenant("tenant-a"), record.id)

    approvals.decide(
        trusted_tenant("tenant-a"), record.id, actor_id="reviewer-1", approved=False
    )
    with pytest.raises(PolicyDenied):
        approvals.require_continue(trusted_tenant("tenant-a"), record.id)


@pytest.mark.parametrize(
    ("domain", "effect", "capability"),
    [
        (PolicyDomain.EXTERNAL_ACTION, EffectClass.EXTERNAL, "crm.export"),
        (PolicyDomain.FINANCIAL_ACTION, EffectClass.FINANCIAL, "payment.capture"),
    ],
)
def test_unauthorized_side_effect_is_rejected(
    domain: PolicyDomain, effect: EffectClass, capability: str
) -> None:
    registry, policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id=f"effect.{capability}",
                mode=PolicyMode.AUTO,
                domain=domain,
                workflows=frozenset({"ops.export"}),
                capability=capability,
                agent_id="ops_agent",
            )
        )
    )
    request = _request(
        f"effect.{capability}",
        agent="ops_agent",
        workflow="ops.export",
        domain=domain,
        effect=effect,
        capability=capability,
    )

    denied = policies.evaluate(request, trusted_tenant("tenant-a"))
    assert denied.status is DecisionStatus.REJECTED
    assert denied.reason == "unauthorized_side_effect"
    assert policies.next_action(denied).action is GateAction.STOP

    registry.grant(
        agent_id="ops_agent", capability=capability, workflows=frozenset({"ops.export"})
    )
    allowed = policies.evaluate(request, trusted_tenant("tenant-a"))
    record = approvals.submit(request, trusted_tenant("tenant-a"))
    continued = approvals.require_continue(trusted_tenant("tenant-a"), record.id)

    assert allowed.status is DecisionStatus.APPROVED
    assert continued.action is GateAction.CONTINUE
    assert continued.publishing_granted is False
    assert continued.executes_effect is False


def test_tenant_isolation_hides_another_tenants_approval() -> None:
    registry, _policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="content.review",
                mode=PolicyMode.MANUAL,
                domain=PolicyDomain.CONTENT_APPROVAL,
                workflows=frozenset({"catalog.review"}),
            )
        )
    )
    request = _request(
        "content.review",
        agent="catalog_agent",
        workflow="catalog.review",
        domain=PolicyDomain.CONTENT_APPROVAL,
        effect=EffectClass.NONE,
    )
    record = approvals.submit(request, trusted_tenant("tenant-a"))
    other = trusted_tenant("tenant-b")

    with pytest.raises(ApprovalNotFound):
        approvals.get(other, record.id)
    with pytest.raises(ApprovalNotFound):
        approvals.gate(other, record.id)
    with pytest.raises(ApprovalNotFound):
        approvals.decide(other, record.id, actor_id="reviewer-2", approved=True)

    untouched = approvals.get(trusted_tenant("tenant-a"), record.id)
    assert untouched.status is DecisionStatus.PENDING
    assert untouched.tenant_id == "tenant-a"


def test_policy_bypass_attempts_are_rejected() -> None:
    registry, policies, approvals = _stack()
    definition = PolicyDefinition(
        id="tool.read",
        mode=PolicyMode.AUTO,
        domain=PolicyDomain.TOOL_ACCESS,
        workflows=frozenset({"catalog.read"}),
        conditions=(
            PolicyCondition(
                field="qa_passed", operator=ConditionOperator.EQ, value=True
            ),
        ),
    )

    class LyingPolicy:
        def __init__(self) -> None:
            self.definition = definition

        def evaluate(self, _subject: object) -> PolicyVerdict:
            return PolicyVerdict(Verdict.ALLOW, "auto_policy")

    class NotAVerdict:
        def __init__(self) -> None:
            self.definition = definition

        def evaluate(self, _subject: object) -> dict[str, str]:
            return {"status": "approved"}

    registry.register(LyingPolicy())
    request = _request(
        "tool.read",
        agent="catalog_agent",
        workflow="catalog.read",
        domain=PolicyDomain.TOOL_ACCESS,
        effect=EffectClass.NONE,
        facts={},
    )
    decision = policies.evaluate(request, trusted_tenant("tenant-a"))
    assert decision.status is DecisionStatus.REJECTED
    assert decision.reason == "condition_failed"

    bypass_facts = [
        {"publish": True},
        {"decision": "approved"},
        {"skip_policy": True},
        {"tenant_id": "tenant-b"},
        {"approved": True},
        {"auto_publish": True},
    ]
    for facts in bypass_facts:
        with pytest.raises(PolicyBypass):
            policies.evaluate(
                _request(
                    "tool.read",
                    agent="catalog_agent",
                    workflow="catalog.read",
                    domain=PolicyDomain.TOOL_ACCESS,
                    effect=EffectClass.NONE,
                    facts=facts,
                ),
                trusted_tenant("tenant-a"),
            )

    with pytest.raises(SecretRejected):
        policies.evaluate(
            _request(
                "tool.read",
                agent="catalog_agent",
                workflow="catalog.read",
                domain=PolicyDomain.TOOL_ACCESS,
                effect=EffectClass.NONE,
                facts={"api_key": "sk-test"},
            ),
            trusted_tenant("tenant-a"),
        )
    with pytest.raises(PolicyBypass):
        registry.register_document(
            {
                "id": "tool.forced",
                "mode": "auto",
                "domain": "tool_access",
                "workflows": ["catalog.read"],
                "publish": True,
            }
        )
    with pytest.raises(InvalidPolicyRequest):
        policies.evaluate(
            {"policy_id": "tool.read", "publish": True}, trusted_tenant("tenant-a")
        )  # type: ignore[arg-type]
    with pytest.raises(InvalidPolicyRequest):
        approvals.submit({"status": "approved"}, trusted_tenant("tenant-a"))  # type: ignore[arg-type]

    other = PolicyRegistry()
    other.register(NotAVerdict())
    with pytest.raises(InvalidPolicyResult):
        PolicyEngine(other).evaluate(
            _request(
                "tool.read",
                agent="catalog_agent",
                workflow="catalog.read",
                domain=PolicyDomain.TOOL_ACCESS,
                effect=EffectClass.NONE,
                facts={"qa_passed": True},
            ),
            trusted_tenant("tenant-a"),
        )

    forged = PolicyDecision(
        status=DecisionStatus.APPROVED,
        reason="auto_policy",
        policy_id="instagram.publication",
        mode=PolicyMode.MANUAL,
        domain=PolicyDomain.PUBLISHING,
        agent_id=INSTAGRAM_AGENT_ID,
        workflow_id="instagram.publish",
        tenant_id="tenant-a",
        capability=PUBLISH_CAPABILITY,
    )
    forged_step = policies.next_action(forged)
    assert forged_step.action is GateAction.WAIT
    assert forged_step.publishing_granted is False
    assert forged_step.executes_effect is False


def test_publishing_is_not_granted_automatically() -> None:
    registry, policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="instagram.publication",
                mode=PolicyMode.MANUAL,
                domain=PolicyDomain.PUBLISHING,
                workflows=APPROVED_PUBLICATION_WORKFLOWS,
                capability=PUBLISH_CAPABILITY,
                agent_id=INSTAGRAM_AGENT_ID,
            )
        )
    )
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="instagram.publication.conditional",
                mode=PolicyMode.CONDITIONAL,
                domain=PolicyDomain.PUBLISHING,
                workflows=frozenset({"instagram.publish"}),
                capability=PUBLISH_CAPABILITY,
                agent_id=INSTAGRAM_AGENT_ID,
                conditions=(
                    PolicyCondition(
                        field="qa_passed", operator=ConditionOperator.EQ, value=True
                    ),
                ),
            )
        )
    )
    registry.grant(
        agent_id=INSTAGRAM_AGENT_ID,
        capability=PUBLISH_CAPABILITY,
        workflows=APPROVED_PUBLICATION_WORKFLOWS,
    )

    with pytest.raises(PolicyRejected, match="Publishing cannot be automatic"):
        PolicyDefinition(
            id="instagram.auto",
            mode=PolicyMode.AUTO,
            domain=PolicyDomain.PUBLISHING,
            workflows=frozenset({"instagram.publish"}),
            capability=PUBLISH_CAPABILITY,
            agent_id=INSTAGRAM_AGENT_ID,
        )
    with pytest.raises(PolicyRejected, match="Publishing cannot be automatic"):
        registry.register_document(
            {
                "id": "instagram.auto",
                "mode": "auto",
                "domain": "publishing",
                "workflows": ["instagram.publish"],
                "capability": PUBLISH_CAPABILITY,
                "agent_id": INSTAGRAM_AGENT_ID,
            }
        )

    request = _request(
        "instagram.publication",
        agent=INSTAGRAM_AGENT_ID,
        workflow="instagram.publish",
        domain=PolicyDomain.PUBLISHING,
        effect=EffectClass.PUBLISHING,
        capability=PUBLISH_CAPABILITY,
    )
    decision = policies.evaluate(request, trusted_tenant("tenant-a"))
    record = approvals.submit(request, trusted_tenant("tenant-a"))

    assert decision.status is DecisionStatus.PENDING
    assert decision.reason == "approval_required"
    assert decision.status is not DecisionStatus.APPROVED
    assert policies.next_action(decision).action is GateAction.WAIT
    assert policies.next_action(decision).publishing_granted is False
    with pytest.raises(MissingApproval):
        approvals.require_continue(trusted_tenant("tenant-a"), record.id)

    conditional = policies.evaluate(
        _request(
            "instagram.publication.conditional",
            agent=INSTAGRAM_AGENT_ID,
            workflow="instagram.publish",
            domain=PolicyDomain.PUBLISHING,
            effect=EffectClass.PUBLISHING,
            capability=PUBLISH_CAPABILITY,
            facts={"qa_passed": True},
        ),
        trusted_tenant("tenant-a"),
    )
    assert conditional.status is DecisionStatus.PENDING
    assert conditional.mode is PolicyMode.CONDITIONAL

    for actor in ("auto", "system", "scheduler", INSTAGRAM_AGENT_ID):
        with pytest.raises(PolicyBypass):
            approvals.decide(
                trusted_tenant("tenant-a"), record.id, actor_id=actor, approved=True
            )
    assert (
        approvals.get(trusted_tenant("tenant-a"), record.id).status
        is DecisionStatus.PENDING
    )

    granted = approvals.decide(
        trusted_tenant("tenant-a"), record.id, actor_id="reviewer-1", approved=True
    )
    step = approvals.require_continue(trusted_tenant("tenant-a"), record.id)
    assert granted.status is DecisionStatus.APPROVED
    assert granted.reason == "human_approved"
    assert step.action is GateAction.CONTINUE
    assert step.publishing_granted is True
    assert step.executes_effect is False


def test_only_instagram_agent_and_approved_workflow_can_publish() -> None:
    registry, policies, approvals = _stack()
    registry.register(
        DeclarativePolicy(
            PolicyDefinition(
                id="instagram.publication",
                mode=PolicyMode.MANUAL,
                domain=PolicyDomain.PUBLISHING,
                workflows=APPROVED_PUBLICATION_WORKFLOWS,
                capability=PUBLISH_CAPABILITY,
                agent_id=INSTAGRAM_AGENT_ID,
            )
        )
    )
    with pytest.raises(PolicyRejected):
        registry.grant(
            agent_id="catalog_agent",
            capability=PUBLISH_CAPABILITY,
            workflows=frozenset({"instagram.publish"}),
        )
    with pytest.raises(PolicyRejected):
        registry.grant(
            agent_id=INSTAGRAM_AGENT_ID,
            capability=PUBLISH_CAPABILITY,
            workflows=frozenset({"studio.generate"}),
        )
    with pytest.raises(PolicyRejected):
        registry.grant(
            agent_id=INSTAGRAM_AGENT_ID,
            capability="publish_instagram_media",
            workflows=frozenset({"instagram.publish"}),
        )
    with pytest.raises(PolicyRejected):
        PolicyDefinition(
            id="other.publish",
            mode=PolicyMode.MANUAL,
            domain=PolicyDomain.PUBLISHING,
            workflows=frozenset({"instagram.publish"}),
            capability=PUBLISH_CAPABILITY,
            agent_id="catalog_agent",
        )

    registry.grant(
        agent_id=INSTAGRAM_AGENT_ID,
        capability=PUBLISH_CAPABILITY,
        workflows=APPROVED_PUBLICATION_WORKFLOWS,
    )
    foreign_agent = policies.evaluate(
        _request(
            "instagram.publication",
            agent="catalog_agent",
            workflow="instagram.publish",
            domain=PolicyDomain.PUBLISHING,
            effect=EffectClass.PUBLISHING,
            capability=PUBLISH_CAPABILITY,
        ),
        trusted_tenant("tenant-a"),
    )
    foreign_workflow = policies.evaluate(
        _request(
            "instagram.publication",
            agent=INSTAGRAM_AGENT_ID,
            workflow="studio.generate",
            domain=PolicyDomain.PUBLISHING,
            effect=EffectClass.PUBLISHING,
            capability=PUBLISH_CAPABILITY,
        ),
        trusted_tenant("tenant-a"),
    )
    missing_capability = policies.evaluate(
        _request(
            "instagram.publication",
            agent=INSTAGRAM_AGENT_ID,
            workflow="studio.approve",
            domain=PolicyDomain.PUBLISHING,
            effect=EffectClass.PUBLISHING,
            capability=None,
        ),
        trusted_tenant("tenant-a"),
    )
    assert foreign_agent.status is DecisionStatus.REJECTED
    assert foreign_agent.reason == "unauthorized_publisher"
    assert foreign_workflow.status is DecisionStatus.REJECTED
    assert foreign_workflow.reason == "unauthorized_workflow"
    assert missing_capability.status is DecisionStatus.REJECTED
    assert missing_capability.reason == "unauthorized_side_effect"

    for workflow_id in (
        "studio.approve",
        "direct.publish",
        "scheduler.campaign",
        "instagram.publish",
    ):
        decision = policies.evaluate(
            _request(
                "instagram.publication",
                agent=INSTAGRAM_AGENT_ID,
                workflow=workflow_id,
                domain=PolicyDomain.PUBLISHING,
                effect=EffectClass.PUBLISHING,
                capability=PUBLISH_CAPABILITY,
            ),
            trusted_tenant("tenant-a"),
        )
        assert decision.status is DecisionStatus.PENDING
        record = approvals.submit(
            _request(
                "instagram.publication",
                agent=INSTAGRAM_AGENT_ID,
                workflow=workflow_id,
                domain=PolicyDomain.PUBLISHING,
                effect=EffectClass.PUBLISHING,
                capability=PUBLISH_CAPABILITY,
            ),
            trusted_tenant("tenant-a"),
        )
        approvals.decide(
            trusted_tenant("tenant-a"), record.id, actor_id="reviewer-1", approved=True
        )
        step = approvals.gate(trusted_tenant("tenant-a"), record.id)
        assert step.publishing_granted is True
        assert step.executes_effect is False


def test_blocked_capability_names_cover_the_workflow_engine() -> None:
    assert WORKFLOW_BLOCKED <= BLOCKED_CAPABILITY_NAMES
    assert PUBLISH_CAPABILITY not in BLOCKED_CAPABILITY_NAMES
    assert "publish_instagram_media" in BLOCKED_CAPABILITY_NAMES
    assert "studio.generate" not in APPROVED_PUBLICATION_WORKFLOWS
    assert "instagram.publish" in APPROVED_PUBLICATION_WORKFLOWS


def test_package_does_not_call_the_instagram_publisher() -> None:
    forbidden_calls = {"eval", "exec", "__import__"}
    forbidden_roots = {
        "agent",
        "tools",
        "services",
        "backend",
        "scheduler",
        "subprocess",
        "importlib",
    }
    for root in ROOTS:
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    for module in modules:
                        assert module.split(".", 1)[0] not in forbidden_roots
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in forbidden_calls
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {
                        "system",
                        "popen",
                        "publish_instagram_media",
                    }
