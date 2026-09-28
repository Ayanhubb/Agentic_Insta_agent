"""Explicit definition of the Instagram Marketing Agent.

The workflow lists the studio's existing order. Handlers and services call
the modules that already implement each step.
"""

from __future__ import annotations

from agent_runtime.catalog import InMemoryCatalog
from agent_runtime.models import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    ExecutionPolicy,
    OwnerKind,
    SideEffect,
    StepKind,
    TriggerBinding,
    VersionStatus,
    WorkflowPin,
    WorkflowStep,
    WorkflowVersion,
)
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS, MCP_TOOL_ALLOWLIST
from backend.platform.assets.catalog import INSTAGRAM_ASSET_ROLES
from backend.platform.knowledge.catalog import INSTAGRAM_KNOWLEDGE_SOURCES
from instagram_reference.handlers import (
    PERSIST_HANDLER_ID,
    PUBLISH_HANDLER_ID,
    VERIFY_HANDLER_ID,
)

AGENT_KEY = "instagram_marketing"
DISPLAY_NAME = "Instagram Marketing Agent"
WORKFLOW_KEY = "instagram.reference"
WORKFLOW_VERSION = 1
AGENT_VERSION = 1
TRIGGER_KEY = "reference.run"
POLICY_ID = "instagram.still_image"

REFERENCE_STEP_KEYS = (
    "load_business_context",
    "load_brand_context",
    "load_product_context",
    "load_festival_context",
    "load_instagram_intelligence",
    "load_trend_evidence",
    "content_planning",
    "image_generation",
    "vision_qa",
    "approval_policy",
    "instagram_agent_publication",
    "meta_verification",
    "persist_result",
)

_OPTIONAL_SOURCES = frozenset({"festival", "account", "trend"})
_MODEL_ROLES = frozenset({"content_planning", "image_generation", "vision_qa"})


def _load(position: int, step_key: str, source: str) -> WorkflowStep:
    return WorkflowStep(
        step_key=step_key,
        position=position,
        kind=StepKind.LOAD_CONTEXT,
        knowledge_source=source,
    )


def _reason(position: int, step_key: str, role: str) -> WorkflowStep:
    return WorkflowStep(
        step_key=step_key,
        position=position,
        kind=StepKind.REASON,
        side_effect=SideEffect.PROVIDER_CALL,
        model_role=role,
    )


def reference_workflow() -> WorkflowVersion:
    """The 13-step marketing workflow. Publish is one closed effect."""

    steps = (
        _load(1, "load_business_context", "business"),
        _load(2, "load_brand_context", "brand"),
        _load(3, "load_product_context", "product"),
        _load(4, "load_festival_context", "festival"),
        _load(5, "load_instagram_intelligence", "account"),
        _load(6, "load_trend_evidence", "trend"),
        _reason(7, "content_planning", "content_planning"),
        _reason(8, "image_generation", "image_generation"),
        _reason(9, "vision_qa", "vision_qa"),
        WorkflowStep(
            step_key="approval_policy",
            position=10,
            kind=StepKind.DECIDE_APPROVAL,
        ),
        WorkflowStep(
            step_key="instagram_agent_publication",
            position=11,
            kind=StepKind.EFFECT,
            side_effect=SideEffect.EXTERNAL_EFFECT,
            handler_id=PUBLISH_HANDLER_ID,
            writes=("agent_tasks", "instagram_posts"),
        ),
        WorkflowStep(
            step_key="meta_verification",
            position=12,
            kind=StepKind.VALIDATE,
            handler_id=VERIFY_HANDLER_ID,
        ),
        WorkflowStep(
            step_key="persist_result",
            position=13,
            kind=StepKind.STORE,
            side_effect=SideEffect.LOCAL_WRITE,
            handler_id=PERSIST_HANDLER_ID,
            writes=("agent_tasks", "instagram_posts"),
        ),
    )
    return WorkflowVersion(
        workflow_key=WORKFLOW_KEY,
        version_number=WORKFLOW_VERSION,
        status=VersionStatus.PUBLISHED,
        steps=steps,
        output_schema="instagram_reference_result",
        approval_policy_id=POLICY_ID,
        side_effects=(SideEffect.PROVIDER_CALL, SideEffect.EXTERNAL_EFFECT, SideEffect.LOCAL_WRITE),
    )


def reference_version() -> AgentVersion:
    return AgentVersion(
        version_number=AGENT_VERSION,
        status=VersionStatus.PUBLISHED,
        workflow_pins=(WorkflowPin(workflow_key=WORKFLOW_KEY, version_number=WORKFLOW_VERSION),),
        trigger_bindings=(TriggerBinding(trigger_key=TRIGGER_KEY, workflow_key=WORKFLOW_KEY),),
        tool_grants=frozenset(MCP_TOOL_ALLOWLIST),
        model_roles=_MODEL_ROLES,
        knowledge_sources=frozenset(source.value for source in INSTAGRAM_KNOWLEDGE_SOURCES),
        asset_roles=frozenset(role.value for role in INSTAGRAM_ASSET_ROLES),
        approval_policy_id=POLICY_ID,
        execution_policy=ExecutionPolicy(timeout_seconds=120),
    )


def reference_definition() -> AgentDefinition:
    return AgentDefinition(
        key=AGENT_KEY,
        owner=OwnerKind.SYSTEM,
        status=AgentStatus.ACTIVE,
        display_name=DISPLAY_NAME,
        published_version_number=AGENT_VERSION,
    )


def assert_publish_boundary(workflow: WorkflowVersion, version: AgentVersion) -> None:
    """Fail registration if a second publisher or a publish tool grant appears."""

    keys = tuple(step.step_key for step in workflow.steps)
    if keys != REFERENCE_STEP_KEYS:
        raise ValueError("The reference workflow steps changed.")
    effects = [
        step
        for step in workflow.steps
        if step.kind == StepKind.EFFECT or step.side_effect == SideEffect.EXTERNAL_EFFECT
    ]
    if len(effects) != 1 or effects[0].handler_id != PUBLISH_HANDLER_ID:
        raise ValueError("Instagram Agent publication is the only external effect.")
    if effects[0].step_key != "instagram_agent_publication":
        raise ValueError("Instagram Agent publication is the only external effect.")
    verification = workflow.steps[keys.index("meta_verification")]
    if verification.handler_id != VERIFY_HANDLER_ID or verification.kind == StepKind.EFFECT:
        raise ValueError("Meta verification is not a second publish step.")
    if any(step.tool_name for step in workflow.steps):
        raise ValueError("The reference workflow does not invoke a publish tool.")
    granted = set(version.tool_grants)
    if granted & set(INSTAGRAM_PUBLISHING_TOOLS):
        raise ValueError("Instagram publishing tools are not granted to the reference agent.")
    if "enqueue_instagram_publication" in granted:
        raise ValueError("The publication handoff is not a tool grant.")
    if not granted <= set(MCP_TOOL_ALLOWLIST):
        raise ValueError("Tool grants must stay inside the MCP read allowlist.")
    sources = {step.knowledge_source for step in workflow.steps if step.knowledge_source}
    if sources != {"business", "brand", "product", "festival", "account", "trend"}:
        raise ValueError("Context steps do not match the reference knowledge sources.")
    optional = {
        step.knowledge_source
        for step in workflow.steps
        if step.knowledge_source in _OPTIONAL_SOURCES
    }
    if optional != _OPTIONAL_SOURCES:
        raise ValueError("Festival, Instagram intelligence, and trend evidence stay optional loads.")
    if set(version.model_roles) != _MODEL_ROLES:
        raise ValueError("Model roles must be planning, image, and vision.")
    if version.approval_policy_id != POLICY_ID or workflow.approval_policy_id != POLICY_ID:
        raise ValueError("Approval policy must be instagram.still_image.")


def instagram_marketing_definition() -> tuple[AgentDefinition, AgentVersion, WorkflowVersion]:
    workflow = reference_workflow()
    version = reference_version()
    definition = reference_definition()
    assert_publish_boundary(workflow, version)
    return definition, version, workflow


def register_instagram_marketing(catalog: InMemoryCatalog | None = None) -> InMemoryCatalog:
    """Store the published system agent. Routes are not changed."""

    store = catalog if catalog is not None else InMemoryCatalog()
    definition, version, workflow = instagram_marketing_definition()
    store.save_definition(
        AgentDefinition(
            key=definition.key,
            owner=definition.owner,
            status=definition.status,
            display_name=definition.display_name,
        )
    )
    store.save_workflow(workflow)
    store.save_version(definition, version)
    store.save_definition(definition)
    return store
