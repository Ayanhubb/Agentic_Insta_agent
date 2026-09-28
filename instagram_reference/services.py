"""Service adapters. Each one calls an existing factory and does not publish."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import ValidationError

from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import ExecutionContext
from ai.model_registry.binding import ProviderRole, instagram_marketing_model
from backend.mcp.errors import ToolNotAllowed, UnknownTool
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS, assert_tool_allowed
from backend.platform.knowledge.binding import instagram_marketing_knowledge
from backend.platform.knowledge.service import PlatformKnowledgeService
from config import Settings
from instagram_reference.boundary import mentions_publish_tool
from models.errors import AppError

_OPTIONAL_SOURCES = frozenset({"festival", "account", "trend"})
Invoker = Callable[[str, dict[str, Any], Any], Awaitable[Mapping[str, Any]]]


class ReferenceKnowledgeService:
    """Loads the reference agent's sources through the existing knowledge service."""

    def __init__(self, inner: PlatformKnowledgeService | None = None) -> None:
        self._inner = inner or PlatformKnowledgeService(instagram_marketing_knowledge())

    async def load(self, ctx: ExecutionContext, source_name: str) -> dict[str, Any]:
        payload = dict(await self._inner.load(ctx, source_name))
        found = bool(payload.get("found"))
        payload["applicable"] = found if source_name in _OPTIONAL_SOURCES else True
        return payload


class ReferenceToolService:
    """MCP reads only. Publishing names are rejected before any invoker runs."""

    def __init__(self, invoker: Invoker | None = None) -> None:
        self._invoker = invoker

    async def invoke(
        self,
        ctx: ExecutionContext,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(tool_name, str) or tool_name in INSTAGRAM_PUBLISHING_TOOLS:
            raise RuntimeFailure(
                RuntimeErrorCode.TOOL_NOT_GRANTED,
                "Instagram publishing tools are not available to this agent.",
            )
        grants = getattr(getattr(ctx, "snapshot", None), "tool_grants", frozenset())
        if tool_name not in grants:
            raise RuntimeFailure(RuntimeErrorCode.TOOL_NOT_GRANTED, "Tool is not granted.")
        try:
            assert_tool_allowed(tool_name)
        except (ToolNotAllowed, UnknownTool) as exc:
            raise RuntimeFailure(RuntimeErrorCode.TOOL_NOT_GRANTED, "Tool is not granted.") from exc
        if self._invoker is None:
            raise RuntimeFailure(RuntimeErrorCode.STEP_FAILED, "Tool service is not configured.")
        result = await self._invoker(tool_name, dict(arguments), ctx.tenant_id)
        return dict(result) if isinstance(result, Mapping) else {"found": False}


class ReferenceModelService:
    """Planning, pixels, and vision QA through the existing provider factories."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    async def complete(
        self,
        ctx: ExecutionContext,
        role: str,
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        if role == "content_planning":
            result = await self._plan(ctx, state)
            if mentions_publish_tool(result):
                return {"planned": False, "provider": _name(result), "reason": "publish_tool_rejected"}
            return result
        if role == "image_generation":
            if mentions_publish_tool(state.get("content_planning")):
                return {
                    "generated": False,
                    "edited": False,
                    "provider": self._implementation(ProviderRole.IMAGE_GENERATE),
                    "reason": "publish_tool_rejected",
                    "malformed": True,
                }
            return await self._image(ctx, state)
        if role == "vision_qa":
            return await self._vision(ctx, state)
        raise RuntimeFailure(RuntimeErrorCode.ROLE_NOT_GRANTED, "Model role is not granted.")

    async def _plan(self, ctx: ExecutionContext, state: Mapping[str, Any]) -> dict[str, Any]:
        del ctx
        provider = self._implementation(ProviderRole.REASONING_STUDIO)
        raw = _incoming(state).get("creative_context")
        if not isinstance(raw, dict):
            return {"planned": False, "provider": provider, "reason": "context_not_assembled"}
        if mentions_publish_tool(raw):
            return {"planned": False, "provider": provider, "reason": "publish_tool_rejected"}
        try:
            from ai.creative_model import get_creative_model
            from models.creative import CreativeContext

            context = CreativeContext.model_validate(raw)
            plan = await get_creative_model(self._resolved_settings()).create_plan(context)
        except (AppError, ValidationError):
            return {"planned": False, "provider": provider, "reason": "plan_rejected", "malformed": True}
        dumped = plan.model_dump(mode="json")
        return {"planned": True, "provider": provider, "plan": dumped}

    async def _image(self, ctx: ExecutionContext, state: Mapping[str, Any]) -> dict[str, Any]:
        provider = self._implementation(ProviderRole.IMAGE_GENERATE)
        planning = state.get("content_planning") if isinstance(state.get("content_planning"), dict) else {}
        plan = planning.get("plan") if isinstance(planning.get("plan"), dict) else {}
        prompt = plan.get("image_prompt")
        if planning.get("planned") is not True or not isinstance(prompt, str) or not prompt.strip():
            return {"generated": False, "edited": False, "provider": provider, "reason": "plan_missing"}
        try:
            from ai.image_generator import select_image_provider
            from ai.schemas import ImageGenerationRequest

            request = ImageGenerationRequest(prompt=prompt.strip(), user_id=ctx.tenant_id)
            generated = await select_image_provider(self._resolved_settings()).generate(request)
        except (AppError, ValidationError):
            return {
                "generated": False,
                "edited": False,
                "provider": provider,
                "reason": "provider_rejected",
                "malformed": True,
            }
        image_id = str(getattr(generated, "id", "") or "").strip()
        image_path = str(getattr(generated, "storage_path", "") or "").strip()
        return {
            "generated": bool(image_id),
            "edited": False,
            "provider": provider,
            "image_id": image_id or None,
            "image_path": image_path or None,
        }

    async def _vision(self, ctx: ExecutionContext, state: Mapping[str, Any]) -> dict[str, Any]:
        from ai.vision.deepseek import get_vision_provider
        from scheduler.image_qa import ImageQAService

        provider = self._implementation(ProviderRole.VISION_QA)
        try:
            vision = get_vision_provider(self._resolved_settings())
        except AppError:
            vision = None
        image = _review_image(state)
        prompt = _image_prompt(state)
        qa = await ImageQAService(vision).review(
            image=image,
            prompt=prompt,
            user_id=ctx.tenant_id,
            correlation_id=ctx.correlation_id,
        )
        return {
            "passed": bool(qa.passed) and not bool(qa.malformed),
            "malformed": bool(qa.malformed),
            "reasons": [str(item)[:120] for item in qa.reasons if item],
            "provider": provider if vision is not None else qa.provider,
        }

    def _resolved_settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings.from_env()
        return self._settings

    def _implementation(self, role: ProviderRole) -> str:
        binding = instagram_marketing_model(self._resolved_settings()).binding_for(role)
        if binding is None:
            return role.value.split(".", 1)[0]
        return binding.implementation


def _incoming(state: Mapping[str, Any]) -> Mapping[str, Any]:
    incoming = state.get("input")
    return incoming if isinstance(incoming, Mapping) else {}


def _name(result: Mapping[str, Any]) -> str:
    provider = result.get("provider")
    return provider if isinstance(provider, str) and provider else "deepseek"


def _image_prompt(state: Mapping[str, Any]) -> str | None:
    planning = state.get("content_planning")
    if not isinstance(planning, dict):
        return None
    plan = planning.get("plan")
    if not isinstance(plan, dict):
        return None
    prompt = plan.get("image_prompt")
    return prompt.strip() if isinstance(prompt, str) and prompt.strip() else None


def _review_image(state: Mapping[str, Any]) -> dict[str, str] | None:
    image = state.get("image_generation")
    if not isinstance(image, dict) or image.get("generated") is not True:
        return None
    image_id = image.get("image_id")
    image_path = image.get("image_path")
    if not isinstance(image_id, str) or not image_id.strip():
        return None
    if not isinstance(image_path, str) or not image_path.strip():
        return None
    return {"id": image_id.strip(), "storage_path": image_path.strip()}
