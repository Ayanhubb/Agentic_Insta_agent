"""Authenticated routes for creating and configuring agents.

Every route uses the session user as the tenant. Another tenant's agent is
not found. Provider secrets and executable code are rejected before a value
is stored, and the rejection does not echo the value. These routes do not
publish to Meta.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from agent_builder.schema import (
    AgentCreate,
    AgentPatch,
    CloneAgent,
    ExecuteRequest,
    RollbackRequest,
    reject_untrusted,
)
from agent_builder.service import AgentBuilderService
from auth.deps import require_password_ok
from db.models import User
from models.errors import AppError, ErrorCode

router = APIRouter(prefix="/agents", tags=["agents"])


def _builder(request: Request) -> AgentBuilderService:
    return request.app.state.agent_builder


async def _object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent configuration must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent configuration must be a JSON object.")
    try:
        reject_untrusted(payload)
    except ValueError as exc:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "Agent configuration cannot include code or provider secrets.",
        ) from exc
    return payload


def _parse(model: type[AgentCreate | AgentPatch | CloneAgent | ExecuteRequest | RollbackRequest], payload: dict[str, Any]):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "Agent configuration could not be validated.") from exc


@router.get("")
async def list_agents(
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).list_agents(user.id)


@router.post("")
async def create_agent(
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    body = _parse(AgentCreate, await _object(request))
    assert isinstance(body, AgentCreate)
    return _builder(request).create_agent(user.id, body)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).get_agent(user.id, agent_id)


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    body = _parse(AgentPatch, await _object(request))
    assert isinstance(body, AgentPatch)
    return _builder(request).update_draft(user.id, agent_id, body)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).retire(user.id, agent_id)


@router.post("/{agent_id}/versions")
async def open_draft(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).open_draft(user.id, agent_id)


@router.post("/{agent_id}/validate")
async def validate_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).validate_agent(user.id, agent_id)


@router.post("/{agent_id}/publish")
async def publish_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).publish(user.id, agent_id)


@router.post("/{agent_id}/disable")
async def disable_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).disable(user.id, agent_id)


@router.post("/{agent_id}/rollback")
async def rollback_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    body = _parse(RollbackRequest, await _object(request))
    assert isinstance(body, RollbackRequest)
    return _builder(request).rollback(user.id, agent_id, body)


@router.post("/{agent_id}/clone")
async def clone_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    body = _parse(CloneAgent, await _object(request))
    assert isinstance(body, CloneAgent)
    return _builder(request).clone_agent(user.id, agent_id, body)


@router.get("/{agent_id}/executions")
async def list_executions(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    return _builder(request).list_executions(user.id, agent_id)


@router.post("/{agent_id}/execute")
async def execute_agent(
    agent_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    body = _parse(ExecuteRequest, await _object(request))
    assert isinstance(body, ExecuteRequest)
    return await _builder(request).execute(user.id, agent_id, body)
