"""Authenticated agent builder routes. Responses name providers and models, never keys."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from auth.deps import require_password_ok
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.platform.builder.catalog import public_catalog
from backend.platform.builder.secrets import (
    assert_no_secrets,
    blank_secret,
    looks_like_secret,
)
from backend.platform.builder.store import AgentBuilderStore
from config import Settings
from db.models import User
from models.errors import AppError, ErrorCode

router = APIRouter(prefix="/platform/agents", tags=["agent-builder"])

_DOTTED = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str
    owner: Literal["system", "user"]
    status: Literal["active", "paused", "retired"]
    read_only: bool
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
    published_version_number: int | None = None
    draft_version_number: int | None = None

    @field_validator(
        "name",
        "description",
        "llm_provider",
        "llm_model",
        "image_provider",
        "image_model",
        "workflow_id",
    )
    @classmethod
    def _text(cls, value: str) -> str:
        return blank_secret(value)

    @field_validator("mcp_tools", "assets", "knowledge_sources", "triggers")
    @classmethod
    def _names(cls, value: list[str]) -> list[str]:
        return [
            item
            for item in value
            if item not in INSTAGRAM_PUBLISHING_TOOLS and not looks_like_secret(item)
        ]


class AgentListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[AgentResponse]


class ExecutionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    status: str
    trigger_key: str
    workflow_id: str
    started_at: str | None = None


class ExecutionListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ExecutionItem]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class ProfileUpdate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    status: Literal["active", "paused", "retired"]


class ModelsUpdate(_Strict):
    llm_provider: str = Field(min_length=1, max_length=64)
    llm_model: str = Field(min_length=1, max_length=128)
    image_provider: str = Field(min_length=1, max_length=64)
    image_model: str = Field(min_length=1, max_length=128)


class ToolUpdate(_Strict):
    mcp_tools: list[str] = Field(default_factory=list, max_length=64)


class AssetUpdate(_Strict):
    assets: list[str] = Field(default_factory=list, max_length=32)


class KnowledgeUpdate(_Strict):
    knowledge_sources: list[str] = Field(default_factory=list, max_length=32)


class TriggerUpdate(_Strict):
    triggers: list[str] = Field(default_factory=list, max_length=16)


class WorkflowUpdate(_Strict):
    workflow_id: str = Field(min_length=1, max_length=80)


class ApprovalUpdate(_Strict):
    approval_policy_id: str = Field(min_length=1, max_length=80)


class CloneBody(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class RollbackBody(_Strict):
    version_number: int = Field(ge=1)


class ExecuteBody(_Strict):
    trigger_key: str | None = None
    version_number: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("trigger_key")
    @classmethod
    def _trigger(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _DOTTED.fullmatch(value) is None:
            raise ValueError("trigger key is not valid")
        return value


def _store(request: Request) -> AgentBuilderStore:
    store = getattr(request.app.state, "platform_agents", None)
    if not isinstance(store, AgentBuilderStore):
        store = AgentBuilderStore()
        request.app.state.platform_agents = store
    return store


def _settings(request: Request) -> Settings:
    return request.app.state.settings


async def _object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent configuration must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent configuration must be a JSON object.")
    assert_no_secrets(payload)
    return payload


def _parse(model: type[_Strict], payload: dict[str, Any]) -> _Strict:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent configuration could not be validated.") from exc


def _agent(payload: dict[str, Any]) -> AgentResponse:
    return AgentResponse.model_validate(payload)


@router.get("/catalog")
def catalog(request: Request, _user: User = Depends(require_password_ok)) -> dict[str, Any]:
    return public_catalog(_settings(request))


@router.get("")
def list_agents(
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentListResponse:
    items = _store(request).list_agents(user.id, _settings(request))
    return AgentListResponse(items=[_agent(item) for item in items])


@router.post("", status_code=201)
async def create_agent(
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(AgentCreate, await _object(request))
    assert isinstance(body, AgentCreate)
    created = _store(request).create(
        user.id,
        _settings(request),
        name=body.name,
        description=body.description,
    )
    return _agent(created)


@router.get("/{agent_id}")
def get_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    return _agent(_store(request).get(user.id, agent_id, _settings(request)))


@router.put("/{agent_id}")
async def update_profile(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(ProfileUpdate, await _object(request))
    assert isinstance(body, ProfileUpdate)
    updated = _store(request).update(
        user.id,
        agent_id,
        _settings(request),
        name=body.name,
        description=body.description,
        status=body.status,
    )
    return _agent(updated)


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    return _agent(_store(request).retire(user.id, agent_id, _settings(request)))


@router.get("/{agent_id}/workflow")
def get_workflow(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _store(request).get(user.id, agent_id, _settings(request))
    catalog_body = public_catalog(_settings(request))
    workflow = next((item for item in catalog_body["workflows"] if item["id"] == agent["workflow_id"]), None)
    if workflow is None:
        raise AppError(ErrorCode.NOT_FOUND, "Workflow not found.")
    return {
        "workflow_id": agent["workflow_id"],
        "read_only": agent["read_only"],
        "label": workflow["label"],
        "summary": workflow["summary"],
        "steps": workflow["steps"],
    }


@router.put("/{agent_id}/workflow")
async def update_workflow(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(WorkflowUpdate, await _object(request))
    assert isinstance(body, WorkflowUpdate)
    updated = _store(request).update(
        user.id,
        agent_id,
        _settings(request),
        workflow_id=body.workflow_id,
    )
    return _agent(updated)


@router.get("/{agent_id}/tools")
def get_tools(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _agent(_store(request).get(user.id, agent_id, _settings(request)))
    return {"mcp_tools": agent.mcp_tools, "read_only": agent.read_only}


@router.put("/{agent_id}/tools")
async def update_tools(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(ToolUpdate, await _object(request))
    assert isinstance(body, ToolUpdate)
    return _agent(
        _store(request).update(user.id, agent_id, _settings(request), mcp_tools=body.mcp_tools)
    )


@router.get("/{agent_id}/models")
def get_models(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _agent(_store(request).get(user.id, agent_id, _settings(request)))
    return {
        "llm_provider": agent.llm_provider,
        "llm_model": agent.llm_model,
        "image_provider": agent.image_provider,
        "image_model": agent.image_model,
        "read_only": agent.read_only,
    }


@router.put("/{agent_id}/models")
async def update_models(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(ModelsUpdate, await _object(request))
    assert isinstance(body, ModelsUpdate)
    return _agent(
        _store(request).update(
            user.id,
            agent_id,
            _settings(request),
            llm_provider=body.llm_provider,
            llm_model=body.llm_model,
            image_provider=body.image_provider,
            image_model=body.image_model,
        )
    )


@router.get("/{agent_id}/assets")
def get_assets(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _agent(_store(request).get(user.id, agent_id, _settings(request)))
    return {"assets": agent.assets, "read_only": agent.read_only}


@router.put("/{agent_id}/assets")
async def update_assets(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(AssetUpdate, await _object(request))
    assert isinstance(body, AssetUpdate)
    return _agent(_store(request).update(user.id, agent_id, _settings(request), assets=body.assets))


@router.get("/{agent_id}/knowledge")
def get_knowledge(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _agent(_store(request).get(user.id, agent_id, _settings(request)))
    return {"knowledge_sources": agent.knowledge_sources, "read_only": agent.read_only}


@router.put("/{agent_id}/knowledge")
async def update_knowledge(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(KnowledgeUpdate, await _object(request))
    assert isinstance(body, KnowledgeUpdate)
    return _agent(
        _store(request).update(
            user.id,
            agent_id,
            _settings(request),
            knowledge_sources=body.knowledge_sources,
        )
    )


@router.get("/{agent_id}/triggers")
def get_triggers(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _agent(_store(request).get(user.id, agent_id, _settings(request)))
    return {"triggers": agent.triggers, "read_only": agent.read_only}


@router.put("/{agent_id}/triggers")
async def update_triggers(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(TriggerUpdate, await _object(request))
    assert isinstance(body, TriggerUpdate)
    return _agent(
        _store(request).update(user.id, agent_id, _settings(request), triggers=body.triggers)
    )


@router.get("/{agent_id}/approval")
def get_approval(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    agent = _agent(_store(request).get(user.id, agent_id, _settings(request)))
    catalog_body = public_catalog(_settings(request))
    policy = next(
        (item for item in catalog_body["approval_policies"] if item["id"] == agent.approval_policy_id),
        None,
    )
    if policy is None:
        raise AppError(ErrorCode.NOT_FOUND, "Approval policy not found.")
    return {
        "approval_policy_id": agent.approval_policy_id,
        "read_only": agent.read_only,
        "label": policy["label"],
        "summary": policy["summary"],
        "gates": policy["gates"],
    }


@router.put("/{agent_id}/approval")
async def update_approval(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(ApprovalUpdate, await _object(request))
    assert isinstance(body, ApprovalUpdate)
    return _agent(
        _store(request).update(
            user.id,
            agent_id,
            _settings(request),
            approval_policy_id=body.approval_policy_id,
        )
    )


@router.get("/{agent_id}/versions")
def list_versions(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    return _store(request).versions(user.id, agent_id, _settings(request))


@router.post("/{agent_id}/versions")
def open_version(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    return _agent(_store(request).open_draft(user.id, agent_id, _settings(request)))


@router.post("/{agent_id}/validate")
def validate_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    return _store(request).validate_agent(user.id, agent_id, _settings(request))


@router.post("/{agent_id}/publish")
def publish_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    return _agent(_store(request).publish(user.id, agent_id, _settings(request)))


@router.post("/{agent_id}/disable")
def disable_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    return _agent(_store(request).disable(user.id, agent_id, _settings(request)))


@router.post("/{agent_id}/clone")
async def clone_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(CloneBody, await _object(request))
    assert isinstance(body, CloneBody)
    created = _store(request).clone_agent(
        user.id,
        agent_id,
        _settings(request),
        name=body.name,
        description=body.description,
    )
    return _agent(created)


@router.post("/{agent_id}/rollback")
async def rollback_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> AgentResponse:
    body = _parse(RollbackBody, await _object(request))
    assert isinstance(body, RollbackBody)
    updated = _store(request).rollback(user.id, agent_id, _settings(request), body.version_number)
    return _agent(updated)


@router.get("/{agent_id}/executions")
def list_executions(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> ExecutionListResponse:
    rows = _store(request).list_executions(user.id, agent_id, _settings(request))
    return ExecutionListResponse(items=[ExecutionItem.model_validate(item) for item in rows])


@router.post("/{agent_id}/execute")
async def execute_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict[str, Any]:
    body = _parse(ExecuteBody, await _object(request))
    assert isinstance(body, ExecuteBody)
    return await _store(request).execute(
        user.id,
        agent_id,
        _settings(request),
        trigger_key=body.trigger_key,
        version_number=body.version_number,
        payload=body.payload,
    )
