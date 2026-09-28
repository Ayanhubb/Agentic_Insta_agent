"""Per-user agent configuration. The store is process memory and holds no secrets."""

from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.platform.builder.catalog import (
    SYSTEM_AGENT_ID,
    default_models,
    public_catalog,
    system_agent_fields,
    workflows,
)
from backend.platform.builder.runner import run_declared, run_reference, run_system
from backend.platform.builder.secrets import blank_secret
from config import Settings
from instagram_reference.definition import WORKFLOW_KEY, reference_workflow
from models.errors import AppError, ErrorCode

_AGENT_ID = re.compile(r"^agent_[a-f0-9]{12}$")
_CONFIG_FIELDS = frozenset(
    {
        "llm_provider",
        "llm_model",
        "image_provider",
        "image_model",
        "mcp_tools",
        "assets",
        "knowledge_sources",
        "triggers",
        "workflow_id",
        "approval_policy_id",
    }
)


@dataclass
class ConfigSnapshot:
    llm_provider: str
    llm_model: str
    image_provider: str
    image_model: str
    mcp_tools: list[str]
    assets: list[str]
    knowledge_sources: list[str]
    workflow_id: str
    triggers: list[str]
    approval_policy_id: str

    def copy(self) -> ConfigSnapshot:
        return ConfigSnapshot(
            llm_provider=self.llm_provider,
            llm_model=self.llm_model,
            image_provider=self.image_provider,
            image_model=self.image_model,
            mcp_tools=list(self.mcp_tools),
            assets=list(self.assets),
            knowledge_sources=list(self.knowledge_sources),
            workflow_id=self.workflow_id,
            triggers=list(self.triggers),
            approval_policy_id=self.approval_policy_id,
        )

    def public(self) -> dict[str, Any]:
        return {
            "llm_provider": blank_secret(self.llm_provider),
            "llm_model": blank_secret(self.llm_model),
            "image_provider": blank_secret(self.image_provider),
            "image_model": blank_secret(self.image_model),
            "mcp_tools": _public_names(self.mcp_tools),
            "assets": _public_names(self.assets),
            "knowledge_sources": _public_names(self.knowledge_sources),
            "workflow_id": self.workflow_id,
            "triggers": _public_names(self.triggers),
            "approval_policy_id": self.approval_policy_id,
        }


@dataclass
class VersionRecord:
    version_number: int
    status: str
    config: ConfigSnapshot

    def public(self) -> dict[str, Any]:
        body = self.config.public()
        body["version_number"] = self.version_number
        body["status"] = self.status
        return body


@dataclass
class AgentRecord:
    id: str
    name: str
    description: str
    owner: str
    status: str
    read_only: bool
    llm_provider: str
    llm_model: str
    image_provider: str
    image_model: str
    mcp_tools: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    knowledge_sources: list[str] = field(default_factory=list)
    workflow_id: str = "studio.generate"
    triggers: list[str] = field(default_factory=list)
    approval_policy_id: str = "human.required"
    published_version_number: int | None = None
    versions: list[VersionRecord] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        draft = _draft(self)
        return {
            "id": self.id,
            "name": blank_secret(self.name),
            "description": blank_secret(self.description),
            "owner": self.owner,
            "status": self.status,
            "read_only": self.read_only,
            "llm_provider": blank_secret(self.llm_provider),
            "llm_model": blank_secret(self.llm_model),
            "image_provider": blank_secret(self.image_provider),
            "image_model": blank_secret(self.image_model),
            "mcp_tools": _public_names(self.mcp_tools),
            "assets": _public_names(self.assets),
            "knowledge_sources": _public_names(self.knowledge_sources),
            "workflow_id": self.workflow_id,
            "triggers": _public_names(self.triggers),
            "approval_policy_id": self.approval_policy_id,
            "published_version_number": self.published_version_number,
            "draft_version_number": None if draft is None else draft.version_number,
        }


def _draft(record: AgentRecord) -> VersionRecord | None:
    drafts = [version for version in record.versions if version.status == "draft"]
    if not drafts:
        return None
    return drafts[-1]


def _snapshot(record: AgentRecord) -> ConfigSnapshot:
    return ConfigSnapshot(
        llm_provider=record.llm_provider,
        llm_model=record.llm_model,
        image_provider=record.image_provider,
        image_model=record.image_model,
        mcp_tools=list(record.mcp_tools),
        assets=list(record.assets),
        knowledge_sources=list(record.knowledge_sources),
        workflow_id=record.workflow_id,
        triggers=list(record.triggers),
        approval_policy_id=record.approval_policy_id,
    )


def _apply(record: AgentRecord, config: ConfigSnapshot) -> None:
    record.llm_provider = config.llm_provider
    record.llm_model = config.llm_model
    record.image_provider = config.image_provider
    record.image_model = config.image_model
    record.mcp_tools = list(config.mcp_tools)
    record.assets = list(config.assets)
    record.knowledge_sources = list(config.knowledge_sources)
    record.workflow_id = config.workflow_id
    record.triggers = list(config.triggers)
    record.approval_policy_id = config.approval_policy_id


def _public_names(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if value in INSTAGRAM_PUBLISHING_TOOLS:
            continue
        text = blank_secret(value)
        if text:
            cleaned.append(text)
    return cleaned


class AgentBuilderStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_user: dict[str, dict[str, AgentRecord]] = {}
        self._executions: list[dict[str, Any]] = []

    def list_agents(self, user_id: str, settings: Settings) -> list[dict[str, Any]]:
        system = AgentRecord(**system_agent_fields(settings)).public()
        with self._lock:
            owned = [
                record.public()
                for record in self._by_user.get(user_id, {}).values()
                if record.status != "retired"
            ]
        owned.sort(key=lambda item: item["name"].lower())
        return [system, *owned]

    def get(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        if agent_id == SYSTEM_AGENT_ID:
            return AgentRecord(**system_agent_fields(settings)).public()
        with self._lock:
            record = self._by_user.get(user_id, {}).get(agent_id)
        if record is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent not found.")
        return record.public()

    def create(self, user_id: str, settings: Settings, *, name: str, description: str) -> dict[str, Any]:
        models = default_models(settings)
        record = AgentRecord(
            id=self._new_id(user_id),
            name=name.strip(),
            description=description.strip(),
            owner="user",
            status="active",
            read_only=False,
            llm_provider=models["llm_provider"],
            llm_model=models["llm_model"],
            image_provider=models["image_provider"],
            image_model=models["image_model"],
            triggers=["manual"],
            approval_policy_id="human.required",
            workflow_id="studio.generate",
        )
        record.versions.append(VersionRecord(1, "draft", _snapshot(record)))
        with self._lock:
            self._by_user.setdefault(user_id, {})[record.id] = record
        return record.public()

    def update(self, user_id: str, agent_id: str, settings: Settings, **changes: Any) -> dict[str, Any]:
        catalog = public_catalog(settings)
        with self._lock:
            record = self._mutable(user_id, agent_id)
            if _CONFIG_FIELDS.intersection(changes) and _draft(record) is None:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "Create a draft version before changing configuration.",
                )
            if "name" in changes:
                record.name = str(changes["name"]).strip()
            if "description" in changes:
                record.description = str(changes["description"]).strip()
            if "status" in changes:
                record.status = str(changes["status"])
            if "llm_provider" in changes:
                record.llm_provider, record.llm_model = _pair(
                    str(changes["llm_provider"]),
                    str(changes["llm_model"]),
                    catalog["llm_providers"],
                    catalog["llm_models"],
                )
            if "image_provider" in changes:
                record.image_provider, record.image_model = _pair(
                    str(changes["image_provider"]),
                    str(changes["image_model"]),
                    catalog["image_providers"],
                    catalog["image_models"],
                )
            if "mcp_tools" in changes:
                record.mcp_tools = _selection(
                    list(changes["mcp_tools"]),
                    [item["name"] for item in catalog["mcp_tools"]],
                    blocked=INSTAGRAM_PUBLISHING_TOOLS,
                )
            if "assets" in changes:
                record.assets = _selection(
                    list(changes["assets"]),
                    [item["id"] for item in catalog["asset_roles"]],
                )
            if "knowledge_sources" in changes:
                record.knowledge_sources = _selection(
                    list(changes["knowledge_sources"]),
                    [item["id"] for item in catalog["knowledge_sources"]],
                )
            if "triggers" in changes:
                selectable = [item["id"] for item in catalog["triggers"] if item.get("selectable", True)]
                record.triggers = _selection(list(changes["triggers"]), selectable)
            if "workflow_id" in changes:
                record.workflow_id = _workflow(str(changes["workflow_id"]), catalog["workflows"])
            if "approval_policy_id" in changes:
                record.approval_policy_id = _policy(
                    str(changes["approval_policy_id"]),
                    catalog["approval_policies"],
                )
            draft = _draft(record)
            if draft is not None and _CONFIG_FIELDS.intersection(changes):
                draft.config = _snapshot(record)
            return record.public()

    def versions(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        if agent_id == SYSTEM_AGENT_ID:
            fields = system_agent_fields(settings)
            config = _snapshot(AgentRecord(**fields))
            return {"versions": [VersionRecord(1, "published", config).public()]}
        with self._lock:
            record = self._owned(user_id, agent_id)
            rows = [version.public() for version in record.versions]
        return {"versions": rows}

    def open_draft(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        del settings
        with self._lock:
            record = self._mutable(user_id, agent_id)
            if _draft(record) is not None:
                raise AppError(ErrorCode.CONFLICT, "An agent can have only one draft version.")
            source_number = record.published_version_number
            source = _version(record, source_number) if source_number is not None else None
            if source is None or source.status != "published":
                raise AppError(ErrorCode.CONFLICT, "Publish the agent before opening another draft.")
            number = max(version.version_number for version in record.versions) + 1
            draft = VersionRecord(number, "draft", source.config.copy())
            record.versions.append(draft)
            _apply(record, draft.config)
            return record.public()

    def validate_agent(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        if agent_id == SYSTEM_AGENT_ID:
            fields = system_agent_fields(settings)
            errors = _problems(_snapshot(AgentRecord(**fields)), settings)
            return {"valid": not errors, "errors": errors}
        with self._lock:
            record = self._owned(user_id, agent_id)
            errors = _problems(_snapshot(record), settings)
        return {"valid": not errors, "errors": errors}

    def publish(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        with self._lock:
            record = self._mutable(user_id, agent_id)
            draft = _draft(record)
            if draft is None:
                if record.published_version_number is None:
                    raise AppError(ErrorCode.INVALID_REQUEST, "Agent has no version to publish.")
                record.status = "active"
                return record.public()
            errors = _problems(_snapshot(record), settings)
            if errors:
                raise AppError(ErrorCode.INVALID_REQUEST, " ".join(errors))
            draft.status = "published"
            draft.config = _snapshot(record)
            record.published_version_number = draft.version_number
            record.status = "active"
            return record.public()

    def disable(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        del settings
        with self._lock:
            record = self._mutable(user_id, agent_id)
            if record.status == "retired":
                raise AppError(ErrorCode.NOT_FOUND, "Agent not found.")
            record.status = "paused"
            return record.public()

    def retire(self, user_id: str, agent_id: str, settings: Settings) -> dict[str, Any]:
        del settings
        with self._lock:
            record = self._mutable(user_id, agent_id)
            record.status = "retired"
            return record.public()

    def clone_agent(
        self,
        user_id: str,
        agent_id: str,
        settings: Settings,
        *,
        name: str | None,
        description: str | None,
    ) -> dict[str, Any]:
        if agent_id == SYSTEM_AGENT_ID:
            source_name, source_description, config = _system_config(settings)
        else:
            with self._lock:
                source = self._owned(user_id, agent_id)
                if source.status == "retired":
                    raise AppError(ErrorCode.NOT_FOUND, "Agent not found.")
                source_name = source.name
                source_description = source.description
                config = _snapshot(source)
        record = AgentRecord(
            id=self._new_id(user_id),
            name=(name or _copy_name(source_name)).strip(),
            description=(source_description if description is None else description).strip(),
            owner="user",
            status="active",
            read_only=False,
            llm_provider=config.llm_provider,
            llm_model=config.llm_model,
            image_provider=config.image_provider,
            image_model=config.image_model,
            mcp_tools=list(config.mcp_tools),
            assets=list(config.assets),
            knowledge_sources=list(config.knowledge_sources),
            workflow_id=config.workflow_id,
            triggers=list(config.triggers),
            approval_policy_id=config.approval_policy_id,
        )
        record.versions.append(VersionRecord(1, "draft", _snapshot(record)))
        with self._lock:
            self._by_user.setdefault(user_id, {})[record.id] = record
        return record.public()

    def rollback(self, user_id: str, agent_id: str, settings: Settings, version_number: int) -> dict[str, Any]:
        del settings
        with self._lock:
            record = self._mutable(user_id, agent_id)
            version = _version(record, version_number)
            if version is None:
                raise AppError(ErrorCode.NOT_FOUND, "Agent version was not found.")
            if version.status != "published":
                raise AppError(ErrorCode.INVALID_REQUEST, "Rollback can only select a published version.")
            record.published_version_number = version.version_number
            if _draft(record) is None:
                _apply(record, version.config)
            return record.public()

    def list_executions(self, user_id: str, agent_id: str, settings: Settings) -> list[dict[str, Any]]:
        self.get(user_id, agent_id, settings)
        with self._lock:
            return [
                dict(item["execution"])
                for item in self._executions
                if item["user_id"] == user_id and item["agent_id"] == agent_id
            ]

    async def execute(
        self,
        user_id: str,
        agent_id: str,
        settings: Settings,
        *,
        trigger_key: str | None,
        version_number: int | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if agent_id == SYSTEM_AGENT_ID:
            fields = system_agent_fields(settings)
            chosen = trigger_key or fields["triggers"][0]
            view = await run_system(user_id, payload, chosen)
            self._remember(user_id, agent_id, view)
            return {"execution": view}
        with self._lock:
            record = self._owned(user_id, agent_id)
            if record.status == "retired":
                raise AppError(ErrorCode.NOT_FOUND, "Agent not found.")
            if record.status == "paused":
                raise AppError(ErrorCode.CONFLICT, "Agent is paused.")
            selected = _select_version(record, version_number)
            if not selected.config.triggers and trigger_key is None:
                raise AppError(ErrorCode.INVALID_REQUEST, "A trigger is required.")
            chosen_trigger = trigger_key or selected.config.triggers[0]
            if chosen_trigger not in selected.config.triggers:
                raise AppError(ErrorCode.INVALID_REQUEST, "Trigger is not bound to this version.")
            spec = {
                "name": record.name,
                "status": record.status,
                "version_number": selected.version_number,
                "version_status": selected.status,
                "workflow_id": selected.config.workflow_id,
                "triggers": list(selected.config.triggers),
                "tools": list(selected.config.mcp_tools),
                "knowledge": list(selected.config.knowledge_sources),
                "assets": list(selected.config.assets),
                "approval_policy_id": selected.config.approval_policy_id,
                "llm_provider": selected.config.llm_provider,
                "image_provider": selected.config.image_provider,
                "trigger_key": chosen_trigger,
            }
        if spec["workflow_id"] == WORKFLOW_KEY:
            view = await run_reference(
                tenant_id=user_id,
                agent_id=agent_id,
                display_name=str(spec["name"]),
                status=str(spec["status"]),
                version_number=int(spec["version_number"]),
                version_status=str(spec["version_status"]),
                workflow_id=str(spec["workflow_id"]),
                triggers=list(spec["triggers"]),
                tools=list(spec["tools"]),
                knowledge=list(spec["knowledge"]),
                assets=list(spec["assets"]),
                approval_policy_id=str(spec["approval_policy_id"]),
                llm_provider=str(spec["llm_provider"]),
                image_provider=str(spec["image_provider"]),
                trigger_key=str(spec["trigger_key"]),
                payload=payload,
            )
        else:
            view = await run_declared(
                execution_id=f"exec_{secrets.token_hex(8)}",
                workflow_id=str(spec["workflow_id"]),
                trigger_key=str(spec["trigger_key"]),
                tenant_id=user_id,
                payload=payload,
                settings=settings,
                version_number=int(spec["version_number"]),
            )
        self._remember(user_id, agent_id, view)
        return {"execution": view}

    def _remember(self, user_id: str, agent_id: str, view: dict[str, Any]) -> None:
        with self._lock:
            self._executions.append({"user_id": user_id, "agent_id": agent_id, "execution": dict(view)})

    def _owned(self, user_id: str, agent_id: str) -> AgentRecord:
        record = self._by_user.get(user_id, {}).get(agent_id)
        if record is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent not found.")
        return record

    def _mutable(self, user_id: str, agent_id: str) -> AgentRecord:
        if agent_id == SYSTEM_AGENT_ID:
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "The Instagram reference agent is read-only.",
            )
        record = self._owned(user_id, agent_id)
        if record.read_only:
            raise AppError(ErrorCode.PERMISSION_ERROR, "This agent is read-only.")
        return record

    def _new_id(self, user_id: str) -> str:
        with self._lock:
            owned = self._by_user.get(user_id, {})
            for _ in range(5):
                agent_id = f"agent_{secrets.token_hex(6)}"
                if agent_id not in owned and _AGENT_ID.fullmatch(agent_id):
                    return agent_id
        raise AppError(ErrorCode.INTERNAL_ERROR, "Could not create an agent id.")


def _version(record: AgentRecord, number: int | None) -> VersionRecord | None:
    if number is None:
        return None
    for version in record.versions:
        if version.version_number == number:
            return version
    return None


def _select_version(record: AgentRecord, requested: int | None) -> VersionRecord:
    if requested is not None:
        version = _version(record, requested)
        if version is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent version was not found.")
        return version
    published = _version(record, record.published_version_number)
    if published is not None and published.status == "published":
        return published
    draft = _draft(record)
    if draft is None:
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent has no version to test.")
    return draft


def _system_config(settings: Settings) -> tuple[str, str, ConfigSnapshot]:
    fields = system_agent_fields(settings)
    record = AgentRecord(**fields)
    return str(fields["name"]), str(fields["description"]), _snapshot(record)


def _copy_name(name: str) -> str:
    return f"Copy of {name}".strip()[:120]


def _workflow_steps(workflow_id: str) -> list[dict[str, Any]]:
    match = next((item for item in workflows() if item["id"] == workflow_id), None)
    if match is None:
        raise AppError(ErrorCode.INVALID_REQUEST, "Workflow is not registered.")
    return [dict(step) for step in match["steps"]]


def _problems(config: ConfigSnapshot, settings: Settings) -> list[str]:
    catalog = public_catalog(settings)
    errors: list[str] = []
    known_workflows = {item["id"] for item in catalog["workflows"]}
    if config.workflow_id not in known_workflows:
        errors.append("Workflow is not registered.")
    if not config.triggers:
        errors.append("A trigger is required.")
    else:
        known_triggers = {item["id"] for item in catalog["triggers"]}
        if any(item not in known_triggers for item in config.triggers):
            errors.append("Trigger is not registered.")
    known_policies = {item["id"] for item in catalog["approval_policies"]}
    if config.approval_policy_id not in known_policies:
        errors.append("Approval policy is not registered.")
    if any(name in INSTAGRAM_PUBLISHING_TOOLS for name in config.mcp_tools):
        errors.append("Instagram publishing tools are not available through agent configuration.")
    if config.workflow_id == WORKFLOW_KEY:
        required = {
            step.knowledge_source
            for step in reference_workflow().steps
            if step.knowledge_source
        }
        for source in sorted(required):
            if source not in config.knowledge_sources:
                errors.append(f"Knowledge source {source} is required.")
    return errors


def _selection(chosen: list[str], allowed: list[str], *, blocked: frozenset[str] | None = None) -> list[str]:
    blocked = blocked or frozenset()
    if any(item in blocked for item in chosen):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "Instagram publishing tools are not available in agent configuration.",
        )
    if len(chosen) != len(set(chosen)) or any(item not in allowed for item in chosen):
        raise AppError(ErrorCode.INVALID_REQUEST, "That option is not registered.")
    chosen_set = set(chosen)
    return [item for item in allowed if item in chosen_set]


def _pair(
    provider: str,
    model: str,
    providers: list[dict[str, Any]],
    models: list[dict[str, str]],
) -> tuple[str, str]:
    if provider not in {item["id"] for item in providers}:
        raise AppError(ErrorCode.INVALID_REQUEST, "That provider is not registered.")
    allowed = {item["id"] for item in models if item["provider"] == provider}
    if model not in allowed:
        raise AppError(ErrorCode.INVALID_REQUEST, "That model is not registered for the provider.")
    return provider, model


def _workflow(workflow_id: str, rows: list[dict[str, Any]]) -> str:
    match = next((item for item in rows if item["id"] == workflow_id), None)
    if match is None or not match.get("selectable", False):
        raise AppError(ErrorCode.INVALID_REQUEST, "That workflow is not available for this agent.")
    return workflow_id


def _policy(policy_id: str, policies: list[dict[str, Any]]) -> str:
    if policy_id not in {item["id"] for item in policies}:
        raise AppError(ErrorCode.INVALID_REQUEST, "That approval policy is not registered.")
    return policy_id
