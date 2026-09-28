"""Tenant-scoped agent configuration on the in-process catalog.

The system agent is seeded from code and is not writable. User agents pin
registered workflows, tools, knowledge sources, asset roles, and policies.
Configuration stores provider names, not API keys.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from agent_builder.schema import (
    AgentCreate,
    AgentPatch,
    CloneAgent,
    ExecuteRequest,
    ModelBindingInput,
    RollbackRequest,
)
from agent_runtime.catalog import InMemoryCatalog, scope_key
from agent_runtime.errors import RuntimeErrorCode
from agent_runtime.models import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionRecord,
    OwnerKind,
    TriggerBinding,
    TriggerContext,
    VersionStatus,
    WorkflowPin,
    WorkflowVersion,
)
from agent_runtime.runtime import AgentRuntime
from agent_runtime.services import ApprovalRegistry, HandlerRegistry
from ai.model_registry.binding import ProviderRole, instagram_marketing_model
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS, MCP_TOOL_ALLOWLIST
from backend.platform.assets.catalog import AssetRole
from backend.platform.knowledge.catalog import KnowledgeSource
from config import Settings
from instagram_reference.definition import (
    AGENT_KEY,
    POLICY_ID,
    TRIGGER_KEY,
    WORKFLOW_KEY,
    WORKFLOW_VERSION,
    register_instagram_marketing,
)
from instagram_reference.handlers import (
    PERSIST_HANDLER_ID,
    PUBLISH_HANDLER_ID,
    VERIFY_HANDLER_ID,
    meta_verification,
    persist_result,
)
from instagram_reference.policy import InstagramStillImagePolicy
from instagram_reference.services import ReferenceKnowledgeService, ReferenceToolService
from models.errors import AppError, ErrorCode

_IMAGE_IMPLEMENTATIONS = frozenset({"openai", "mock"})
_VISION_IMPLEMENTATIONS = frozenset({"deepseek", "mock"})
_PLAN_IMPLEMENTATIONS = frozenset({"deepseek", "openai", "mock"})
_ROLE_IMPLEMENTATIONS = {
    "content_planning": _PLAN_IMPLEMENTATIONS,
    "image_generation": _IMAGE_IMPLEMENTATIONS,
    "vision_qa": _VISION_IMPLEMENTATIONS,
}


class AgentBuilderService:
    """Create and configure user agents. The reference agent stays read-only."""

    def __init__(
        self,
        settings: Settings | None = None,
        catalog: InMemoryCatalog | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._catalog = catalog if catalog is not None else register_instagram_marketing()
        self._lock = threading.Lock()
        self._configs: dict[tuple[str, int], tuple[ModelBindingInput, ...]] = {}
        self._meta: dict[tuple[str, int], dict[str, Any]] = {}
        self._owned: dict[str, set[str]] = {}
        self._executions: list[ExecutionRecord] = []
        self._descriptions: dict[str, str] = {}
        self._seed_system()
        self._load_persisted()

    def list_agents(self, tenant_id: str) -> dict[str, Any]:
        with self._lock:
            agents = [self._public(self._system(), tenant_id)]
            for key in sorted(self._owned.get(tenant_id, ())):
                definition = self._catalog.get_definition(key, tenant_id=tenant_id)
                if definition is None or definition.owner != OwnerKind.USER:
                    continue
                if definition.status == AgentStatus.RETIRED:
                    continue
                agents.append(self._public(definition, tenant_id))
        return {"agents": agents}

    def get_agent(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            definition = self._visible(tenant_id, agent_key)
            return {"agent": self._public(definition, tenant_id)}

    def create_agent(self, tenant_id: str, body: AgentCreate) -> dict[str, Any]:
        with self._lock:
            self._reject_reserved(body.key)
            if self._owned_definition(tenant_id, body.key) is not None:
                raise AppError(ErrorCode.CONFLICT, "An agent with this key already exists.")
            definition = AgentDefinition(
                key=body.key,
                owner=OwnerKind.USER,
                status=AgentStatus.ACTIVE,
                display_name=body.display_name,
                user_id=tenant_id,
            )
            version, models = self._draft_from_template(1)
            self._catalog.save_definition(definition)
            self._catalog.save_version(definition, version)
            self._remember(definition, version, models)
            self._owned.setdefault(tenant_id, set()).add(body.key)
            self._descriptions[scope_key(definition)] = body.description.strip()
            self._persist(tenant_id, definition)
            return {"agent": self._public(definition, tenant_id)}

    def update_draft(self, tenant_id: str, agent_key: str, body: AgentPatch) -> dict[str, Any]:
        with self._lock:
            definition = self._writable(tenant_id, agent_key)
            draft_number = self._draft_number(definition)
            if draft_number is None:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "Create a draft version before changing configuration.",
                )
            version = self._require_version(definition, draft_number)
            if version.status != VersionStatus.DRAFT:
                raise AppError(ErrorCode.CONFLICT, "A published agent version is immutable.")
            updated_definition = definition
            if body.display_name is not None:
                updated_definition = self._replace(definition, display_name=body.display_name)
            if body.description is not None:
                self._descriptions[scope_key(definition)] = body.description.strip()
            models = self._models_for(definition, draft_number)
            config = dict(self._meta_for(definition, draft_number))
            if body.models is not None:
                models = tuple(body.models)
            if body.tool_names is not None:
                config["tool_names"] = self._tools(body.tool_names)
            if body.knowledge_sources is not None:
                config["knowledge_sources"] = self._knowledge(body.knowledge_sources)
            if body.asset_roles is not None:
                config["asset_roles"] = self._assets(body.asset_roles)
            if body.workflow_key is not None:
                self._require_workflow(body.workflow_key)
                config["workflow_key"] = body.workflow_key
            if body.trigger_key is not None:
                config["trigger_key"] = body.trigger_key
            if body.approval_policy_id is not None:
                self._require_policy(body.approval_policy_id)
                config["approval_policy_id"] = body.approval_policy_id
            if body.timeout_seconds is not None:
                config["timeout_seconds"] = body.timeout_seconds
            replacement = self._version_from(config, models, draft_number, VersionStatus.DRAFT)
            self._catalog.save_version(updated_definition, replacement)
            self._remember(updated_definition, replacement, models)
            self._persist(tenant_id, updated_definition)
            return {"agent": self._public(updated_definition, tenant_id)}

    def open_draft(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            definition = self._writable(tenant_id, agent_key)
            if self._draft_number(definition) is not None:
                raise AppError(ErrorCode.CONFLICT, "An agent can have only one draft version.")
            source_number = definition.published_version_number
            if source_number is None:
                raise AppError(ErrorCode.CONFLICT, "Publish the agent before opening another draft.")
            source = self._require_version(definition, source_number)
            number = source_number + 1
            while self._catalog.get_version(definition, number) is not None:
                number += 1
            models = self._models_for(definition, source.version_number)
            config = dict(self._meta_for(definition, source.version_number))
            draft = self._version_from(config, models, number, VersionStatus.DRAFT)
            self._catalog.save_version(definition, draft)
            self._remember(definition, draft, models)
            self._persist(tenant_id, definition)
            return {"agent": self._public(definition, tenant_id)}

    def validate_agent(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            definition = self._visible(tenant_id, agent_key)
            number = self._draft_number(definition) or definition.published_version_number
            if number is None:
                return {"valid": False, "errors": ["Agent has no version to validate."]}
            errors = self._errors(definition, number)
            return {"valid": not errors, "errors": errors}

    def publish(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            definition = self._writable(tenant_id, agent_key)
            draft_number = self._draft_number(definition)
            if draft_number is None:
                if definition.published_version_number is None:
                    raise AppError(ErrorCode.INVALID_REQUEST, "Agent has no version to publish.")
                active = self._replace(definition, status=AgentStatus.ACTIVE)
                self._persist(tenant_id, active)
                return {"agent": self._public(active, tenant_id)}
            errors = self._errors(definition, draft_number)
            if errors:
                raise AppError(ErrorCode.INVALID_REQUEST, " ".join(errors))
            models = self._models_for(definition, draft_number)
            config = self._meta_for(definition, draft_number)
            published = self._version_from(config, models, draft_number, VersionStatus.PUBLISHED)
            self._catalog.save_version(definition, published)
            self._remember(definition, published, models)
            active = self._replace(
                definition,
                status=AgentStatus.ACTIVE,
                published_version_number=draft_number,
            )
            self._persist(tenant_id, active)
            return {"agent": self._public(active, tenant_id)}

    def disable(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            definition = self._writable(tenant_id, agent_key)
            if definition.status == AgentStatus.RETIRED:
                raise AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
            paused = self._replace(definition, status=AgentStatus.PAUSED)
            self._persist(tenant_id, paused)
            return {"agent": self._public(paused, tenant_id)}

    def retire(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            definition = self._writable(tenant_id, agent_key)
            retired = self._replace(definition, status=AgentStatus.RETIRED)
            self._persist(tenant_id, retired)
            return {"agent": self._public(retired, tenant_id)}

    def clone_agent(self, tenant_id: str, agent_key: str, body: CloneAgent) -> dict[str, Any]:
        with self._lock:
            source = self._visible(tenant_id, agent_key)
            if source.status == AgentStatus.RETIRED:
                raise AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
            self._reject_reserved(body.key)
            if self._owned_definition(tenant_id, body.key) is not None:
                raise AppError(ErrorCode.CONFLICT, "An agent with this key already exists.")
            number = self._draft_number(source) or source.published_version_number
            if number is None:
                raise AppError(ErrorCode.CONFLICT, "Agent has no version to clone.")
            display_name = body.display_name or _copy_name(source.display_name)
            definition = AgentDefinition(
                key=body.key,
                owner=OwnerKind.USER,
                status=AgentStatus.ACTIVE,
                display_name=display_name,
                user_id=tenant_id,
            )
            models = self._models_for(source, number)
            config = dict(self._meta_for(source, number))
            draft = self._version_from(config, models, 1, VersionStatus.DRAFT)
            self._catalog.save_definition(definition)
            self._catalog.save_version(definition, draft)
            self._remember(definition, draft, models)
            self._owned.setdefault(tenant_id, set()).add(body.key)
            self._descriptions[scope_key(definition)] = self._descriptions.get(scope_key(source), "")
            self._persist(tenant_id, definition)
            return {"agent": self._public(definition, tenant_id)}

    def rollback(self, tenant_id: str, agent_key: str, body: RollbackRequest) -> dict[str, Any]:
        with self._lock:
            definition = self._writable(tenant_id, agent_key)
            version = self._catalog.get_version(definition, body.version_number)
            if version is None:
                raise AppError(ErrorCode.NOT_FOUND, "Agent version was not found.")
            if version.status != VersionStatus.PUBLISHED:
                raise AppError(ErrorCode.INVALID_REQUEST, "Rollback can only select a published version.")
            updated = self._replace(definition, published_version_number=body.version_number)
            self._persist(tenant_id, updated)
            return {"agent": self._public(updated, tenant_id)}

    def list_executions(self, tenant_id: str, agent_key: str) -> dict[str, Any]:
        with self._lock:
            self._visible(tenant_id, agent_key)
            rows = [
                record.public_dict()
                for record in self._executions
                if record.tenant_id == tenant_id and record.agent_key == agent_key
            ]
        return {"executions": rows}

    async def execute(self, tenant_id: str, agent_key: str, body: ExecuteRequest) -> dict[str, Any]:
        with self._lock:
            definition = self._visible(tenant_id, agent_key)
            if definition.status == AgentStatus.RETIRED:
                raise AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
            if definition.status == AgentStatus.PAUSED:
                raise AppError(ErrorCode.CONFLICT, "Agent is paused.")
            if body.version_number is not None:
                number = body.version_number
            elif definition.published_version_number is not None:
                number = definition.published_version_number
            else:
                number = self._draft_number(definition)
            if number is None:
                raise AppError(ErrorCode.INVALID_REQUEST, "Agent has no version to test.")
            config = self._meta_for(definition, number)
            models = self._models_for(definition, number)
            trigger_key = body.trigger_key or str(config["trigger_key"])
            bindings = {item.role: item.implementation for item in models}
        runtime = _test_runtime(self._catalog, bindings)
        trigger = TriggerContext(
            trigger_key=trigger_key,
            tenant_id=tenant_id,
            user_id=tenant_id,
            payload=dict(body.payload),
            mode=ExecutionMode.TEST,
            requested_version_number=body.version_number,
        )
        record = await runtime.run(definition.key, trigger)
        if record.error is not None and not record.steps and record.agent_version_number is None:
            raise _runtime_http(record)
        with self._lock:
            self._executions.append(record)
        return {"execution": record.public_dict()}

    def _seed_system(self) -> None:
        definition = self._catalog.get_definition(AGENT_KEY, tenant_id="system")
        if definition is None:
            raise AppError(ErrorCode.CONFIGURATION_ERROR, "The reference agent is not registered.")
        version = self._catalog.get_version(definition, definition.published_version_number or 1)
        if version is None:
            raise AppError(ErrorCode.CONFIGURATION_ERROR, "The reference agent is not registered.")
        models = _template_models(self._settings)
        self._remember(definition, version, models)
        self._descriptions[scope_key(definition)] = (
            "The Instagram studio is this agent's interface. Configuration here is read-only."
        )
        self._persist("system", definition)

    def _persist(self, tenant_id: str, definition: AgentDefinition) -> None:
        if self._sessions is None:
            return
        public = self._public(definition, tenant_id)
        from db.platform_agents import PlatformStoreError, sync_agent_public

        session = self._sessions()
        try:
            sync_agent_public(session, tenant_id=tenant_id, agent=public)
            session.commit()
        except PlatformStoreError as exc:
            session.rollback()
            raise AppError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _load_persisted(self) -> None:
        if self._sessions is None:
            return
        from sqlalchemy import select

        from db.models import DurableAgent, DurableAgentVersion

        session = self._sessions()
        try:
            agents = session.scalars(select(DurableAgent).where(DurableAgent.owner == "user")).all()
            for agent in agents:
                versions = session.scalars(
                    select(DurableAgentVersion)
                    .where(DurableAgentVersion.agent_id == agent.id)
                    .order_by(DurableAgentVersion.version_number)
                ).all()
                self._hydrate(agent, list(versions))
        finally:
            session.close()

    def _hydrate(self, agent: Any, versions: list[Any]) -> None:
        if agent.agent_key == AGENT_KEY or agent.tenant_id in self._owned and agent.agent_key in self._owned.get(agent.tenant_id, ()):
            return
        definition = AgentDefinition(
            key=agent.agent_key,
            owner=OwnerKind.USER,
            status=AgentStatus(agent.status),
            display_name=agent.display_name,
            user_id=agent.tenant_id,
            published_version_number=agent.published_version_number,
        )
        self._catalog.save_definition(definition)
        self._owned.setdefault(agent.tenant_id, set()).add(agent.agent_key)
        self._descriptions[scope_key(definition)] = agent.description or ""
        for version in versions:
            snapshot = version.snapshot if isinstance(version.snapshot, dict) else {}
            models = tuple(
                ModelBindingInput(
                    role=item["role"],
                    implementation=item["implementation"],
                    model=str(item.get("model") or ""),
                )
                for item in snapshot.get("models") or []
                if isinstance(item, dict)
            )
            config = {
                "workflow_key": snapshot.get("workflow_key") or WORKFLOW_KEY,
                "trigger_key": snapshot.get("trigger_key") or TRIGGER_KEY,
                "approval_policy_id": snapshot.get("approval_policy_id") or POLICY_ID,
                "tool_names": snapshot.get("tool_names") or [],
                "knowledge_sources": snapshot.get("knowledge_sources") or [],
                "asset_roles": snapshot.get("asset_roles") or [],
                "timeout_seconds": snapshot.get("timeout_seconds") or 120,
            }
            built = self._version_from(config, models, int(version.version_number), VersionStatus(version.status))
            self._catalog.save_version(definition, built)
            self._remember(definition, built, models)

    def _draft_from_template(self, number: int) -> tuple[AgentVersion, tuple[ModelBindingInput, ...]]:
        definition = self._system()
        source = definition.published_version_number or 1
        models = self._models_for(definition, source)
        config = dict(self._meta_for(definition, source))
        return self._version_from(config, models, number, VersionStatus.DRAFT), models

    def _system(self) -> AgentDefinition:
        definition = self._catalog.get_definition(AGENT_KEY, tenant_id="system")
        if definition is None:
            raise AppError(ErrorCode.CONFIGURATION_ERROR, "The reference agent is not registered.")
        return definition

    def _visible(self, tenant_id: str, agent_key: str) -> AgentDefinition:
        if agent_key == AGENT_KEY:
            return self._system()
        definition = self._owned_definition(tenant_id, agent_key)
        if definition is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
        return definition

    def _writable(self, tenant_id: str, agent_key: str) -> AgentDefinition:
        if agent_key == AGENT_KEY:
            raise AppError(ErrorCode.PERMISSION_ERROR, "The system agent cannot be changed.")
        return self._visible(tenant_id, agent_key)

    def _owned_definition(self, tenant_id: str, agent_key: str) -> AgentDefinition | None:
        if agent_key not in self._owned.get(tenant_id, ()):
            return None
        definition = self._catalog.get_definition(agent_key, tenant_id=tenant_id)
        if definition is None or definition.owner != OwnerKind.USER or definition.user_id != tenant_id:
            return None
        return definition

    def _reject_reserved(self, agent_key: str) -> None:
        if agent_key == AGENT_KEY:
            raise AppError(ErrorCode.CONFLICT, "This agent key is reserved.")

    def _replace(self, definition: AgentDefinition, **changes: Any) -> AgentDefinition:
        updated = definition.model_copy(update=changes)
        self._catalog.save_definition(updated)
        return updated

    def _remember(
        self,
        definition: AgentDefinition,
        version: AgentVersion,
        models: tuple[ModelBindingInput, ...],
    ) -> None:
        slot = (scope_key(definition), version.version_number)
        self._configs[slot] = models
        workflow_key = version.workflow_pins[0].workflow_key if version.workflow_pins else WORKFLOW_KEY
        trigger_key = version.trigger_bindings[0].trigger_key if version.trigger_bindings else TRIGGER_KEY
        self._meta[slot] = {
            "tool_names": tuple(sorted(version.tool_grants)),
            "knowledge_sources": tuple(sorted(version.knowledge_sources)),
            "asset_roles": tuple(sorted(version.asset_roles)),
            "workflow_key": workflow_key,
            "trigger_key": trigger_key,
            "approval_policy_id": version.approval_policy_id or POLICY_ID,
            "timeout_seconds": version.execution_policy.timeout_seconds,
        }

    def _models_for(self, definition: AgentDefinition, number: int) -> tuple[ModelBindingInput, ...]:
        return self._configs.get((scope_key(definition), number), ())

    def _meta_for(self, definition: AgentDefinition, number: int) -> dict[str, Any]:
        meta = self._meta.get((scope_key(definition), number))
        if meta is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent version was not found.")
        return meta

    def _require_version(self, definition: AgentDefinition, number: int) -> AgentVersion:
        version = self._catalog.get_version(definition, number)
        if version is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent version was not found.")
        return version

    def _draft_number(self, definition: AgentDefinition) -> int | None:
        draft = self._catalog.get_draft(definition)
        if draft is None:
            return None
        return draft.version_number

    def _version_numbers(self, definition: AgentDefinition) -> list[int]:
        numbers = [
            number
            for (scope, number) in self._meta
            if scope == scope_key(definition) and self._catalog.get_version(definition, number) is not None
        ]
        return sorted(numbers)

    def _errors(self, definition: AgentDefinition, number: int) -> list[str]:
        version = self._catalog.get_version(definition, number)
        if version is None:
            return ["Agent version was not found."]
        config = self._meta.get((scope_key(definition), number))
        if config is None:
            return ["Agent version was not found."]
        errors: list[str] = []
        workflow_key = str(config["workflow_key"])
        workflow = self._catalog.get_workflow(workflow_key, WORKFLOW_VERSION)
        if workflow is None or workflow_key != WORKFLOW_KEY:
            errors.append("Workflow is not registered.")
            workflow = None
        if str(config["approval_policy_id"]) != POLICY_ID:
            errors.append("Approval policy is not registered.")
        if not version.trigger_bindings:
            errors.append("A trigger is required.")
        models = self._models_for(definition, number)
        roles = {item.role for item in models}
        if workflow is not None:
            for step in workflow.steps:
                if step.knowledge_source and step.knowledge_source not in version.knowledge_sources:
                    errors.append(f"Knowledge source {step.knowledge_source} is required.")
                if step.model_role and step.model_role not in roles:
                    errors.append(f"Model role {step.model_role} is required.")
                if step.tool_name:
                    errors.append("Workflow steps cannot name a tool.")
        overlap = set(version.tool_grants) & set(INSTAGRAM_PUBLISHING_TOOLS)
        if overlap:
            errors.append("Instagram publishing tools are not available through agent configuration.")
        if "enqueue_instagram_publication" in version.tool_grants:
            errors.append("The publication handoff is not a tool grant.")
        return errors

    def _version_from(
        self,
        config: Mapping[str, Any],
        models: tuple[ModelBindingInput, ...],
        number: int,
        status: VersionStatus,
    ) -> AgentVersion:
        workflow_key = str(config["workflow_key"])
        self._require_workflow(workflow_key)
        self._require_policy(str(config["approval_policy_id"]))
        tools = self._tools(config["tool_names"])
        knowledge = self._knowledge(config["knowledge_sources"])
        assets = self._assets(config["asset_roles"])
        roles = frozenset(item.role for item in models)
        for item in models:
            if item.implementation not in _ROLE_IMPLEMENTATIONS[item.role]:
                raise AppError(ErrorCode.INVALID_REQUEST, "That provider is not available for this role.")
        return AgentVersion(
            version_number=number,
            status=status,
            workflow_pins=(WorkflowPin(workflow_key=workflow_key, version_number=WORKFLOW_VERSION),),
            trigger_bindings=(
                TriggerBinding(trigger_key=str(config["trigger_key"]), workflow_key=workflow_key),
            ),
            tool_grants=frozenset(tools),
            model_roles=roles,
            knowledge_sources=frozenset(knowledge),
            asset_roles=frozenset(assets),
            approval_policy_id=str(config["approval_policy_id"]),
            execution_policy=ExecutionPolicy(timeout_seconds=float(config["timeout_seconds"])),
        )

    def _public(self, definition: AgentDefinition, tenant_id: str) -> dict[str, Any]:
        versions = []
        for number in self._version_numbers(definition):
            version = self._catalog.get_version(definition, number)
            if version is None:
                continue
            config = self._meta.get((scope_key(definition), number), {})
            models = [
                {"role": item.role, "implementation": item.implementation, "model": item.model}
                for item in self._models_for(definition, number)
            ]
            versions.append(
                {
                    "version_number": number,
                    "status": version.status.value,
                    "workflow_key": config.get("workflow_key"),
                    "trigger_key": config.get("trigger_key"),
                    "approval_policy_id": version.approval_policy_id,
                    "tool_names": list(config.get("tool_names", ())),
                    "knowledge_sources": list(config.get("knowledge_sources", ())),
                    "asset_roles": list(config.get("asset_roles", ())),
                    "models": models,
                    "timeout_seconds": config.get("timeout_seconds"),
                }
            )
        draft = self._draft_number(definition)
        return {
            "key": definition.key,
            "display_name": definition.display_name,
            "description": self._descriptions.get(scope_key(definition), ""),
            "owner": definition.owner.value,
            "status": definition.status.value,
            "published_version_number": definition.published_version_number,
            "draft_version_number": draft,
            "writable": definition.owner == OwnerKind.USER and definition.user_id == tenant_id,
            "versions": versions,
        }

    def _tools(self, names: Any) -> tuple[str, ...]:
        if isinstance(names, str):
            raise AppError(ErrorCode.INVALID_REQUEST, "Tool configuration needs a list of registered tool names.")
        resolved: list[str] = []
        for name in names:
            if not isinstance(name, str):
                raise AppError(ErrorCode.INVALID_REQUEST, "Tool name is not valid.")
            if name in INSTAGRAM_PUBLISHING_TOOLS:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    "Instagram publishing tools are not available through agent configuration.",
                )
            if name not in MCP_TOOL_ALLOWLIST:
                raise AppError(ErrorCode.INVALID_REQUEST, "Tool is not registered.")
            if name not in resolved:
                resolved.append(name)
        return tuple(sorted(resolved))

    def _knowledge(self, names: Any) -> tuple[str, ...]:
        registered = {item.value for item in KnowledgeSource}
        return _names(names, registered, "Knowledge source is not registered.")

    def _assets(self, names: Any) -> tuple[str, ...]:
        registered = {item.value for item in AssetRole}
        return _names(names, registered, "Asset role is not registered.")

    def _require_workflow(self, workflow_key: str) -> WorkflowVersion:
        workflow = self._catalog.get_workflow(workflow_key, WORKFLOW_VERSION)
        if workflow is None or workflow_key != WORKFLOW_KEY:
            raise AppError(ErrorCode.INVALID_REQUEST, "Workflow is not registered.")
        return workflow

    def _require_policy(self, policy_id: str) -> None:
        if policy_id != POLICY_ID:
            raise AppError(ErrorCode.INVALID_REQUEST, "Approval policy is not registered.")


def _names(names: Any, registered: set[str], message: str) -> tuple[str, ...]:
    if isinstance(names, str):
        raise AppError(ErrorCode.INVALID_REQUEST, message)
    resolved: list[str] = []
    for name in names:
        if not isinstance(name, str) or name not in registered:
            raise AppError(ErrorCode.INVALID_REQUEST, message)
        if name not in resolved:
            resolved.append(name)
    return tuple(sorted(resolved))


def _copy_name(display_name: str) -> str:
    text = f"Copy of {display_name}".strip()
    return text[:120]


def _template_models(settings: Settings | None) -> tuple[ModelBindingInput, ...]:
    defaults = {
        "content_planning": ("deepseek", ""),
        "image_generation": ("openai", ""),
        "vision_qa": ("deepseek", ""),
    }
    if settings is not None:
        config = instagram_marketing_model(settings)
        mapping = {
            "content_planning": ProviderRole.REASONING_STUDIO,
            "image_generation": ProviderRole.IMAGE_GENERATE,
            "vision_qa": ProviderRole.VISION_QA,
        }
        for role, provider_role in mapping.items():
            binding = config.binding_for(provider_role)
            if binding is None:
                continue
            implementation = binding.implementation
            if implementation not in _ROLE_IMPLEMENTATIONS[role]:
                implementation = defaults[role][0]
            model_name = binding.model
            try:
                defaults[role] = (
                    implementation,
                    ModelBindingInput(role=role, implementation=implementation, model=model_name).model,
                )
            except ValueError:
                defaults[role] = (implementation, "")
    return tuple(
        ModelBindingInput(role=role, implementation=implementation, model=model)
        for role, (implementation, model) in defaults.items()
    )


def _test_runtime(catalog: InMemoryCatalog, bindings: Mapping[str, str]) -> AgentRuntime:
    handlers = HandlerRegistry()
    handlers.register(PUBLISH_HANDLER_ID, _test_publication)
    handlers.register(VERIFY_HANDLER_ID, meta_verification)
    handlers.register(PERSIST_HANDLER_ID, persist_result)
    approvals = ApprovalRegistry()
    approvals.register(POLICY_ID, InstagramStillImagePolicy())
    return AgentRuntime(
        catalog=catalog,
        handlers=handlers,
        tools=ReferenceToolService(),
        models=_TestModelService(bindings),
        knowledge=ReferenceKnowledgeService(),
        approvals=approvals,
    )


class _TestModelService:
    """Reports the configured provider name. It does not call a vendor."""

    def __init__(self, bindings: Mapping[str, str]) -> None:
        self._bindings = dict(bindings)

    async def complete(self, ctx: object, role: str, state: Mapping[str, Any]) -> dict[str, Any]:
        del ctx, state
        return {
            "role": role,
            "provider": self._bindings.get(role, "mock"),
            "planned": False,
            "generated": False,
            "edited": False,
            "passed": False,
            "malformed": True,
            "reason": "test_execution",
        }


async def _test_publication(ctx: object, step: object, state: Mapping[str, Any]) -> dict[str, Any]:
    del ctx, step, state
    return {
        "publisher": "instagram_agent",
        "published": False,
        "verified": False,
        "skip_publish": False,
        "instagram_media_id": None,
        "reason": "test_execution",
    }


def _runtime_http(record: ExecutionRecord) -> AppError:
    code = None if record.error is None else record.error.code
    message = "Agent test did not start."
    if record.error is not None and record.error.message:
        message = record.error.message
    if code == RuntimeErrorCode.AGENT_NOT_FOUND:
        return AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
    if code == RuntimeErrorCode.AGENT_PAUSED:
        return AppError(ErrorCode.CONFLICT, "Agent is paused.")
    return AppError(ErrorCode.INVALID_REQUEST, message)
