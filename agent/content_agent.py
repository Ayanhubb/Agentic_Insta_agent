"""Content Strategy Agent. Plans content and generates images. Never publishes."""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent.planner import ALLOWED_TOOL_SET
from backend.ai.image.base import ImageRequest
from backend.integrations.canva.execution import (
    canva_selected,
    image_from_canva,
    unavailable_code,
    unavailable_message,
)
from backend.mcp.errors import MCPError
from backend.mcp.tenant_isolation import trusted_tenant
from models.content import (
    ApprovalStatus,
    AutomationSettingsSnapshot,
    BusinessProfileSnapshot,
    ContentMode,
    ContentPlan,
    ContentSource,
    ContentStrategyRequest,
    ContentStrategyResult,
    ContentTask,
    ContentTaskStatus,
    ContentType,
    DiversityVerdict,
    FestivalContext,
    GeneratedImageSnapshot,
    InstagramHandoff,
    RecentContent,
)
from models.errors import AppError, ErrorCode
from services.asset_resolution import (
    AssetState,
    owner_business_id,
    resolve_brand_logo,
    resolve_owned_asset,
    resolve_product_assets,
)
from services.image_reference import (
    accepts_content_agent_caller,
    asset_ids_from_mcp,
    bound_reference_images,
    submit_creative_image,
)
from services.logging import log_step

logger = logging.getLogger(__name__)

CONTENT_TYPE_ALIASES = {
    "product": ContentType.PRODUCT,
    "product_promotion": ContentType.PROMOTION,
    "promotion": ContentType.PROMOTION,
    "brand": ContentType.BRAND,
    "lifestyle": ContentType.LIFESTYLE,
    "educational": ContentType.EDUCATIONAL,
    "seasonal": ContentType.SEASONAL,
    "festival": ContentType.FESTIVAL,
    "new_arrival": ContentType.NEW_ARRIVAL,
    "customer_focused": ContentType.CUSTOMER_FOCUSED,
}

FORBIDDEN_KEYS = {
    "command",
    "code",
    "script",
    "python",
    "shell",
    "url",
    "http",
    "https",
    "token",
    "access_token",
    "tool",
    "tools",
    "eval",
    "exec",
}

GENERIC_FESTIVAL_MARKERS = (
    "happy diwali fireworks",
    "generic diwali",
    "generic diwali wishes",
    "festive greetings for everyone",
    "fireworks and diyas festive greetings",
    "for everyone celebrating",
    "festive greetings",
    "generic greeting",
)

SYSTEM_PROMPT = """You are a content strategist for a single business's Instagram still images.
Return only a JSON object. You cannot publish to Instagram, run tools, execute code, or access tokens.
Ground every plan in the supplied business profile. Festival images must ONLY belong to this business
and must not be generic greeting cards.
"""


def _tokens(text: str) -> set[str]:
    return {part for part in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


def _flag_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "required"}
    return False


class TokenOverlapSimilarity:
    def score(self, left: str, right: str) -> float:
        a, b = _tokens(left), _tokens(right)
        if not a or not b:
            return 0.0
        return len(a & b) / max(1, len(a | b))


TokenOverlapDiversity = TokenOverlapSimilarity


class SemanticSimilarityBackend:
    """Extension point for embeddings. Disabled in V1."""

    enabled = False

    def score(self, left: str, right: str) -> float:
        if not self.enabled:
            return 0.0
        raise NotImplementedError("Semantic similarity is not implemented in V1.")


class DiversityPolicy:
    def __init__(
        self,
        prompt_threshold: float = 0.72,
        max_same_type: int = 3,
        overlap: TokenOverlapSimilarity | None = None,
        semantic: SemanticSimilarityBackend | None = None,
    ) -> None:
        self.prompt_threshold = prompt_threshold
        self.max_same_type = max_same_type
        self.overlap = overlap or TokenOverlapSimilarity()
        self.semantic = semantic or SemanticSimilarityBackend()

    def evaluate(self, plan: ContentPlan, history: list[RecentContent]) -> DiversityVerdict:
        incoming = f"{plan.theme} {plan.image_prompt} {plan.featured_product_or_service or ''}"
        best = 0.0
        collided = None
        same_type = 0
        for item in history:
            previous = item.prompt_text()
            score = self.overlap.score(incoming, previous)
            if self.semantic.enabled:
                score = max(score, self.semantic.score(incoming, previous))
            if score > best:
                best = score
                collided = item.theme or previous[:80]
            if (item.content_type or "").upper() == plan.content_type.value:
                same_type += 1
        if best >= self.prompt_threshold:
            return DiversityVerdict(
                accepted=False,
                reason="recent_content_too_similar",
                score=best,
                collided_with=collided,
                backend="token_overlap_v1",
                blocking=True,
            )
        if same_type >= self.max_same_type:
            return DiversityVerdict(
                accepted=False,
                reason="content_type_overused",
                score=best,
                backend="token_overlap_v1",
                blocking=True,
            )
        return DiversityVerdict(accepted=True, reason="distinct_enough", score=best, backend="token_overlap_v1", blocking=False)


TokenOverlapDiversity = DiversityPolicy


class SessionContextStore:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def get_business_profile(self, user_id: str) -> BusinessProfileSnapshot | None:
        from db.repositories import BusinessRepository

        profile = BusinessRepository(self._session).get_for_user(user_id)
        return BusinessProfileSnapshot.model_validate(profile) if profile else None

    async def get_automation_settings(self, user_id: str) -> AutomationSettingsSnapshot | None:
        from db.repositories import AutomationRepository

        row = AutomationRepository(self._session).get_or_create(user_id)
        return AutomationSettingsSnapshot.model_validate(row)

    async def list_recent_posts(self, user_id: str, limit: int = 10) -> list[RecentContent]:
        from db.repositories import PostRepository

        return [
            RecentContent(id=item.id, source=item.post_type, status=item.status, created_at=item.created_at)
            for item in PostRepository(self._session).list_for_user(user_id, limit=limit)
        ]

    async def list_recent_generated(self, user_id: str, limit: int = 10) -> list[RecentContent]:
        from db.repositories import GeneratedImageRepository

        return [
            RecentContent(
                id=item.id,
                content_type=item.content_type,
                theme=item.theme,
                original_prompt=item.original_prompt,
                enhanced_prompt=item.enhanced_prompt,
                source=item.source,
                created_at=item.created_at,
            )
            for item in GeneratedImageRepository(self._session).list_for_user(user_id, limit=limit)
        ]

    async def get_festival_context(self, user_id: str, on_date: datetime) -> FestivalContext | None:
        return None

    async def save_generated_image(self, record: GeneratedImageSnapshot) -> GeneratedImageSnapshot:
        from db.repositories import GeneratedImageRepository

        row = GeneratedImageRepository(self._session).create(
            id=record.id or str(uuid4()),
            user_id=record.user_id or "",
            original_prompt=record.original_prompt or "",
            enhanced_prompt=record.enhanced_prompt or record.original_prompt or "",
            model=record.model,
            provider=record.provider,
            filename=record.filename or "img.png",
            storage_path=record.storage_path or "",
            mime_type=record.mime_type or "image/png",
            width=record.width,
            height=record.height,
            generation_status=record.generation_status,
            approval_status=record.approval_status,
            publication_status="GENERATED",
            source=record.source or ContentSource.USER_PROMPT.value,
            content_type=record.content_type,
            theme=record.theme,
        )
        record.id = row.id
        return record

    async def save_content_task(self, task: Any) -> Any:
        return task


class ContentAgent:
    def __init__(
        self,
        llm: Any,
        image_generator: Any,
        context_store: Any | None = None,
        *,
        session: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        diversity: DiversityPolicy | None = None,
        max_plan_attempts: int = 3,
        images: Any | None = None,
        event_sink: Any | None = None,
        settings: Any | None = None,
        mcp: Any | None = None,
        canva: Any | None = None,
    ) -> None:
        self._llm = llm
        self._images = image_generator if image_generator is not None else images
        self._session = session
        self._settings = settings
        self._mcp = mcp
        self._canva = canva
        if context_store is not None:
            self._store = context_store
        elif session is not None:
            self._store = SessionContextStore(session)
        else:
            self._store = None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._diversity = diversity or DiversityPolicy()
        self._max_plan_attempts = max(1, max_plan_attempts)
        self._event_sink = event_sink

    def bind_event_sink(self, sink: Any | None) -> None:
        self._event_sink = sink

    def _emit_stage(self, name: str, request: ContentStrategyRequest, **detail: Any) -> None:
        status = str(detail.pop("status", "success"))
        sink = self._event_sink
        if sink is not None and hasattr(sink, "record"):
            sink.record(
                name,
                status=status,
                task_id=request.task_id,
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                **detail,
            )
            return
        log_step(
            logger,
            event=name,
            task_id=request.task_id or "-",
            request_id=request.request_id,
            status=status,
            correlation_id=request.correlation_id,
        )

    async def create_plan(self, request: ContentStrategyRequest) -> ContentPlan:
        result = await self._plan(request, generate_image=False)
        return result.plan

    async def run(self, request: ContentStrategyRequest) -> ContentStrategyResult:
        return await self._plan(request, generate_image=request.generate_image)

    async def _plan(self, request: ContentStrategyRequest, *, generate_image: bool) -> ContentStrategyResult:
        mode = request.mode
        source = {
            ContentMode.USER_PROMPT: ContentSource.USER_PROMPT,
            ContentMode.DAILY: ContentSource.DAILY_AUTOMATION,
            ContentMode.FESTIVAL: ContentSource.FESTIVAL_AUTOMATION,
        }[mode]
        profile = await self._load_profile(request)
        if profile is None:
            raise AppError(ErrorCode.CONTENT_PROFILE_MISSING, "A business profile is required.")
        automation = await self._load_automation(request)
        if mode == ContentMode.DAILY and automation and not automation.daily_enabled:
            raise AppError(ErrorCode.CONTENT_AUTOMATION_DISABLED, "Daily automation is disabled.")
        if mode == ContentMode.FESTIVAL and automation and not automation.festival_enabled:
            raise AppError(ErrorCode.CONTENT_AUTOMATION_DISABLED, "Festival automation is disabled.")
        if mode == ContentMode.USER_PROMPT and not (request.user_prompt or "").strip():
            raise AppError(ErrorCode.INVALID_REQUEST, "A prompt is required.")

        history = await self._history(request)
        festival = request.festival
        if festival and not isinstance(festival, FestivalContext):
            festival = FestivalContext.model_validate(festival)
        if festival is None and self._store and hasattr(self._store, "get_festival_context"):
            festival = await self._store.get_festival_context(request.user_id, self._clock())
        if mode == ContentMode.FESTIVAL and (festival is None or not getattr(festival, "display_name", None)):
            raise AppError(ErrorCode.INVALID_REQUEST, "Festival mode requires festival context for this business.")

        last_error: AppError | None = None
        plan: ContentPlan | None = None
        diversity = DiversityVerdict(accepted=True, reason="n/a")
        attempts = 0
        for attempts in range(1, self._max_plan_attempts + 1):
            try:
                raw = await self._call_llm(request, profile, automation, festival, history, attempt=attempts)
                self._reject_policy(raw)
                plan = self._ground_catalog(self._parse_plan(raw, mode, source), profile)
                if mode == ContentMode.FESTIVAL:
                    self._reject_generic_festival(plan, profile)
                diversity = self._diversity.evaluate(plan, history)
                if diversity.accepted:
                    break
                last_error = AppError(ErrorCode.CONTENT_DIVERSITY_REJECTED, "Recent content is too similar.")
                plan = None
            except AppError as exc:
                last_error = exc
                plan = None
                if exc.code in {ErrorCode.CONTENT_PROFILE_MISSING, ErrorCode.CONTENT_AUTOMATION_DISABLED}:
                    raise
                continue
        if plan is None:
            raise last_error or AppError(ErrorCode.CONTENT_INVALID_PLAN, "Could not create a content plan.")

        self._emit_stage("CONTENT_PLAN_CREATED", request, status="success", mode=mode.value)
        generated = None
        if generate_image:
            self._emit_stage("IMAGE_GENERATION_STARTED", request, status="success")
            generated = await self._generate_image(request, plan, source)
            if self._store and hasattr(self._store, "save_generated_image"):
                generated = await self._store.save_generated_image(generated)
            self._emit_stage("IMAGE_GENERATED", request, status="success", image_id=generated.id if generated else None)

        auto_approve = False
        if mode == ContentMode.DAILY and automation and automation.auto_daily_publish:
            auto_approve = True
        if mode == ContentMode.FESTIVAL and automation and automation.auto_festival_publish:
            auto_approve = True
        if mode == ContentMode.USER_PROMPT:
            auto_approve = False
        approval = ApprovalStatus.AUTO_APPROVED if auto_approve else ApprovalStatus.PENDING_APPROVAL
        if generated is not None:
            generated.approval_status = approval.value
        status = (
            ContentTaskStatus.APPROVED_PENDING_PUBLISH if auto_approve else ContentTaskStatus.AWAITING_APPROVAL
        )
        task = ContentTask(
            id=request.task_id or str(uuid4()),
            user_id=request.user_id,
            mode=mode,
            source=source,
            status=status,
            plan=plan,
            generated_image_id=generated.id if generated else None,
            approval_status=approval,
            requires_approval=not auto_approve,
            handoff_to_instagram=auto_approve,
            published=False,
        )
        if self._store and hasattr(self._store, "save_content_task"):
            await self._store.save_content_task(task)
        handoff = InstagramHandoff(
            ready=auto_approve and generated is not None,
            user_id=request.user_id,
            generated_image_id=generated.id if generated else None,
            image_path=generated.storage_path if generated else None,
            caption=plan.caption_hint,
            source=source.value,
            content_type=plan.content_type.value,
            instagram_account_id=request.instagram_account_id,
            requires_instagram_agent=True,
            auto_approved=auto_approve,
        )
        return ContentStrategyResult(
            task=task,
            plan=plan,
            generated_image=generated,
            approval_status=approval,
            diversity=diversity,
            handoff=handoff,
            published=False,
            llm_attempts=attempts,
            mode=mode,
            source=source,
        )

    async def _load_profile(self, request: ContentStrategyRequest) -> BusinessProfileSnapshot | None:
        if request.business_profile is not None:
            raw = request.business_profile
            return raw if isinstance(raw, BusinessProfileSnapshot) else BusinessProfileSnapshot.model_validate(raw)
        if self._store and hasattr(self._store, "get_business_profile"):
            return await self._store.get_business_profile(request.user_id)
        return None

    async def _load_automation(self, request: ContentStrategyRequest) -> AutomationSettingsSnapshot | None:
        if request.automation is not None:
            raw = request.automation
            return raw if isinstance(raw, AutomationSettingsSnapshot) else AutomationSettingsSnapshot.model_validate(raw)
        if self._store and hasattr(self._store, "get_automation_settings"):
            return await self._store.get_automation_settings(request.user_id)
        return None

    async def _history(self, request: ContentStrategyRequest) -> list[RecentContent]:
        items: list[RecentContent] = []
        for raw in [*request.recent_posts, *request.recent_generated]:
            items.append(raw if isinstance(raw, RecentContent) else RecentContent.model_validate(raw))
        if items:
            return items
        if self._store:
            if hasattr(self._store, "list_recent_posts"):
                items.extend(await self._store.list_recent_posts(request.user_id))
            if hasattr(self._store, "list_recent_generated"):
                items.extend(await self._store.list_recent_generated(request.user_id))
        return items

    async def _call_llm(
        self,
        request: ContentStrategyRequest,
        profile: BusinessProfileSnapshot | None,
        automation: AutomationSettingsSnapshot | None,
        festival: FestivalContext | None,
        history: list[RecentContent],
        *,
        attempt: int,
    ) -> Any:
        now = request.now or self._clock()
        payload = {
            "current_date": now.date().isoformat(),
            "mode": request.mode.value,
            "user_prompt": request.user_prompt,
            "business_profile": profile.model_dump() if profile else None,
            "automation": automation.model_dump() if automation else None,
            "festival": festival.model_dump() if festival else None,
            "recent_content": [item.model_dump() for item in history[:12]],
            "attempt": attempt,
        }
        user_prompt = json.dumps(payload, default=str)
        try:
            if hasattr(self._llm, "generate_structured"):
                return await self._llm.generate_structured(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema={"type": "object"},
                )
            if hasattr(self._llm, "generate_content_plan"):
                from ai.schemas import (
                    BusinessProfileContext,
                    ContentHistoryItem,
                    ContentPlanRequest,
                    FestivalContext as LLMFestival,
                )

                biz = None
                if profile:
                    products = profile.products if isinstance(profile.products, list) else []
                    services = profile.services if isinstance(profile.services, list) else []
                    biz = BusinessProfileContext(
                        business_name=profile.business_name,
                        business_type=profile.business_type,
                        products=products if isinstance(products, list) else [],
                        services=services if isinstance(services, list) else [],
                    )
                fest = None
                if festival:
                    fest = LLMFestival(
                        festival_name=festival.display_name,
                        festival_date=str(festival.date) if festival.date else None,
                        year=festival.year,
                    )
                plan = await self._llm.generate_content_plan(
                    ContentPlanRequest(
                        user_prompt=request.user_prompt,
                        business_profile=biz,
                        festival=fest,
                        recent_content=[
                            ContentHistoryItem(theme=item.theme, content_type=item.content_type, image_prompt=item.prompt_text())
                            for item in history
                        ],
                        current_date=now.date().isoformat(),
                        reason_hint=request.mode.value.lower(),
                    )
                )
                return plan.model_dump()
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ErrorCode.OPENAI_API_ERROR, "The language model request failed.") from exc
        raise AppError(ErrorCode.OPENAI_CONFIGURATION_ERROR, "No language model provider is configured.")

    def _reject_policy(self, raw: Any) -> None:
        if isinstance(raw, dict):
            keys = {str(key).lower() for key in raw.keys()}
            if keys & FORBIDDEN_KEYS:
                raise AppError(ErrorCode.CONTENT_POLICY_VIOLATION, "The content plan included disallowed fields.")
            blob = json.dumps(raw).lower()
        else:
            blob = str(raw).lower()
        if any(name in blob for name in ALLOWED_TOOL_SET):
            raise AppError(ErrorCode.CONTENT_POLICY_VIOLATION, "The content plan attempted to select a publish tool.")
        if any(token in blob for token in ("os.system", "subprocess", "shell=true", "eval(", "exec(")):
            raise AppError(ErrorCode.CONTENT_POLICY_VIOLATION, "The content plan attempted to execute code.")

    def _ground_catalog(self, plan: ContentPlan, profile: BusinessProfileSnapshot) -> ContentPlan:
        """Name a catalog product when a product plan omitted one.

        Automatic approval requires that name. The catalog stays the source of truth.
        """
        if str(plan.featured_product_or_service or "").strip() or plan.product_ids:
            return plan
        if plan.content_type not in {ContentType.PRODUCT, ContentType.PROMOTION, ContentType.NEW_ARRIVAL}:
            return plan
        raw = profile.products
        if isinstance(raw, str):
            names = [part.strip() for part in re.split(r"[,;\n]", raw) if part.strip()]
        elif isinstance(raw, (list, tuple)):
            names = [str(item).strip() for item in raw if str(item).strip()]
        else:
            names = []
        if not names:
            return plan
        return plan.model_copy(update={"featured_product_or_service": names[0], "product_ids": [names[0]]})

    def _parse_plan(self, raw: Any, mode: ContentMode, source: ContentSource) -> ContentPlan:
        if isinstance(raw, ContentPlan):
            return raw
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AppError(ErrorCode.OPENAI_INVALID_RESPONSE, "The language model returned invalid JSON.") from exc
        if not isinstance(raw, dict):
            raise AppError(ErrorCode.OPENAI_INVALID_RESPONSE, "The language model returned an invalid plan.")
        content_type = str(raw.get("content_type") or "").strip()
        mapped = CONTENT_TYPE_ALIASES.get(content_type.lower().replace(" ", "_").replace("-", "_"))
        if mapped is None:
            try:
                mapped = ContentType(content_type.upper())
            except ValueError as exc:
                raise AppError(ErrorCode.OPENAI_INVALID_RESPONSE, "The content type is not supported.") from exc
        try:
            theme = str(raw.get("theme") or "").strip()
            image_prompt = str(raw.get("image_prompt") or "").strip()
            business_context = str(raw.get("business_context") or "").strip()
            reason = str(raw.get("reason") or "").strip()
            if not theme or not image_prompt or not business_context or not reason or len(image_prompt) < 20:
                raise AppError(ErrorCode.OPENAI_INVALID_RESPONSE, "The language model omitted required plan fields.")
            product_ids = raw.get("product_ids") or []
            if isinstance(product_ids, str):
                product_ids = [product_ids]
            asset_ids = raw.get("asset_ids") or []
            if isinstance(asset_ids, str):
                asset_ids = [asset_ids]
            requirements = raw.get("qa_requirements")
            return ContentPlan(
                content_type=mapped,
                theme=theme,
                image_prompt=image_prompt,
                business_context=business_context,
                reason=reason,
                caption_hint=raw.get("caption_hint") or raw.get("caption"),
                featured_product_or_service=raw.get("featured_product_or_service"),
                mode=mode,
                source=source,
                product_ids=[str(item) for item in product_ids if str(item).strip()],
                asset_ids=[str(item) for item in asset_ids if str(item).strip()],
                logo_required=_flag_true(raw.get("logo_required")),
                logo_asset_id=raw.get("logo_asset_id"),
                offer_text=raw.get("offer_text"),
                qa_requirements=requirements if isinstance(requirements, dict) else None,
                canva_action=raw.get("canva_action"),
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ErrorCode.OPENAI_INVALID_RESPONSE, "The language model returned an invalid plan.") from exc

    def _reject_generic_festival(self, plan: ContentPlan, profile: BusinessProfileSnapshot | None) -> None:
        prompt = f"{plan.image_prompt} {plan.business_context} {plan.theme} {plan.reason}".lower()
        if any(marker in prompt for marker in GENERIC_FESTIVAL_MARKERS):
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "Festival content must be specific to the business.")
        if profile is None:
            return
        products = profile.products if isinstance(profile.products, list) else []
        services = profile.services if isinstance(profile.services, list) else []
        business_blob = " ".join(
            [
                profile.business_name or "",
                profile.business_type or "",
                profile.business_category or "",
                profile.location or "",
                profile.brand_style or "",
                *[str(item) for item in products],
                *[str(item) for item in services],
            ]
        )
        business_tokens = _tokens(business_blob)
        prompt_tokens = _tokens(prompt)
        if business_tokens and prompt_tokens.isdisjoint(business_tokens):
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "Festival content must be specific to the business.")
        leftover = prompt_tokens - {
            "diwali",
            "deepavali",
            "holi",
            "eid",
            "fireworks",
            "diya",
            "diyas",
            "festive",
            "festival",
            "greetings",
            "greeting",
            "wishes",
            "happy",
            "everyone",
            "celebrating",
            "generic",
        }
        if not leftover:
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "Festival content must be specific to the business.")

    async def _generate_image(
        self,
        request: ContentStrategyRequest,
        plan: ContentPlan,
        source: ContentSource,
    ) -> GeneratedImageSnapshot:
        if canva_selected(plan.canva_action):
            return await self._generate_canva(request, plan, source)
        if self._images is None:
            raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, "Image generation is not configured.")
        try:
            if hasattr(self._images, "generate"):
                generated = self._images.generate
                logo, product_image, extras = await self._reference_inputs(request, plan)
                rich = ImageRequest(
                    prompt=plan.image_prompt[:4000],
                    user_id=request.user_id,
                    company_logo=logo,
                    product_image=product_image,
                    reference_images=bound_reference_images(
                        company_logo=logo,
                        product_image=product_image,
                        extras=extras,
                    ),
                )
                if rich.has_input_images or accepts_content_agent_caller(generated):
                    result = await submit_creative_image(self._images, rich, settings=self._settings)
                else:
                    try:
                        result = await generated(
                            prompt=plan.image_prompt,
                            user_id=request.user_id,
                            source=source.value,
                        )
                    except TypeError:
                        from ai.schemas import ImageGenerationRequest

                        result = await generated(
                            ImageGenerationRequest(
                                prompt=plan.image_prompt,
                                user_id=request.user_id,
                                original_prompt=request.user_prompt or plan.theme,
                                source=source.value,  # type: ignore[arg-type]
                            )
                        )
            else:
                raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, "Image generation is not configured.")
        except AppError as exc:
            if exc.code == ErrorCode.OPENAI_API_ERROR:
                raise
            raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, exc.message, http_status=exc.http_status) from exc
        if isinstance(result, GeneratedImageSnapshot):
            return result
        return GeneratedImageSnapshot(
            id=getattr(result, "id", str(uuid4())),
            user_id=request.user_id,
            original_prompt=getattr(result, "original_prompt", plan.theme),
            enhanced_prompt=getattr(result, "enhanced_prompt", plan.image_prompt),
            model=getattr(result, "model", None),
            provider=getattr(result, "provider", None),
            filename=getattr(result, "filename", None),
            storage_path=getattr(result, "storage_path", None),
            mime_type=getattr(result, "mime_type", "image/png"),
            width=getattr(result, "width", None),
            height=getattr(result, "height", None),
            source=source.value,
            content_type=plan.content_type.value,
            theme=plan.theme,
        )

    async def _generate_canva(
        self,
        request: ContentStrategyRequest,
        plan: ContentPlan,
        source: ContentSource,
    ) -> GeneratedImageSnapshot:
        produce = getattr(self._canva, "produce", None)
        if not callable(produce):
            raise AppError(ErrorCode.CANVA_NOT_CONNECTED, unavailable_message("CANVA_NOT_CONNECTED"))
        brief = plan.image_prompt[:4000]
        result = produce(
            user_id=request.user_id,
            action=str(plan.canva_action),
            brief=brief,
            asset_ids=list(plan.asset_ids),
        )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, GeneratedImageSnapshot):
            return result
        if isinstance(result, dict):
            code = unavailable_code(result)
            if code:
                raise AppError(ErrorCode(code), unavailable_message(code))
            shaped = image_from_canva(result, user_id=request.user_id, prompt=brief, settings=self._settings)
            return GeneratedImageSnapshot(
                id=shaped.id,
                user_id=request.user_id,
                original_prompt=brief,
                enhanced_prompt=brief,
                model=shaped.model,
                provider=shaped.provider,
                filename=shaped.filename,
                storage_path=shaped.storage_path,
                mime_type=shaped.mime_type,
                width=shaped.width,
                height=shaped.height,
                source=source.value,
                content_type=plan.content_type.value,
                theme=plan.theme,
            )
        raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva did not return an image.")

    async def _reference_inputs(self, request: ContentStrategyRequest, plan: ContentPlan):
        """Load logo and product files the creative actually asked for.

        MCP decides which ids this tenant may see. The resolver then classifies
        each file. Only AVAILABLE bytes are attached. The prompt is not rewritten
        into a stand-in such as "Use the company logo".
        """
        if self._mcp is None or self._session is None or self._settings is None:
            return None, None, []
        try:
            source = "scheduler" if request.mode in {ContentMode.DAILY, ContentMode.FESTIVAL} else "authenticated"
            tenant = trusted_tenant(request.user_id, source=source)
        except MCPError:
            return None, None, []
        business_id = owner_business_id(self._session, request.user_id)
        logo = await self._owned_logo(tenant, request.user_id, business_id, plan)
        product_image = None
        extras: list[Any] = []
        seen: set[str] = set()
        if logo is not None and plan.logo_asset_id:
            seen.add(plan.logo_asset_id)
        for token in _featured_tokens(plan):
            for resolved in await self._resolved_product_images(tenant, request.user_id, token):
                if not resolved.available or not resolved.asset_id or resolved.asset_id in seen:
                    continue
                seen.add(resolved.asset_id)
                if product_image is None:
                    product_image = resolved.image
                else:
                    extras.append(resolved.image)
        for asset_id in plan.asset_ids:
            if asset_id in seen:
                continue
            resolved = resolve_owned_asset(self._session, self._settings, request.user_id, asset_id)
            if not resolved.available or resolved.image is None:
                continue
            seen.add(resolved.asset_id or asset_id)
            extras.append(resolved.image)
        return logo, product_image, extras

    async def _owned_logo(self, tenant: Any, user_id: str, business_id: str | None, plan: ContentPlan):
        requested = (plan.logo_asset_id or "").strip()
        if not plan.logo_required and not requested:
            return None
        if requested:
            payload = await self._mcp_data(tenant, "get_company_logo", {"asset_id": requested})
            if payload.get("found"):
                resolved = resolve_brand_logo(
                    self._session,
                    self._settings,
                    user_id,
                    business_id=business_id,
                    asset_id=requested,
                )
                if resolved.available:
                    plan.logo_asset_id = resolved.asset_id
                    return resolved.image
        payload = await self._mcp_data(tenant, "get_company_logo", {})
        if not payload.get("found"):
            plan.logo_asset_id = None
            return None
        resolved = resolve_brand_logo(self._session, self._settings, user_id, business_id=business_id)
        if resolved.available:
            plan.logo_asset_id = resolved.asset_id
            return resolved.image
        plan.logo_asset_id = None
        return None

    async def _resolved_product_images(self, tenant: Any, user_id: str, token: str) -> list[Any]:
        product_id = await self._visible_product_id(tenant, user_id, token)
        if product_id:
            return resolve_product_assets(self._session, self._settings, user_id, product_id)
        if " " not in token and len(token) <= 64:
            probed = resolve_product_assets(self._session, self._settings, user_id, token)
            if probed and probed[0].state is AssetState.UNAUTHORIZED:
                return []
        payload = await self._product_image_payload(tenant, token)
        images = []
        for asset_id in asset_ids_from_mcp(payload):
            resolved = resolve_owned_asset(
                self._session,
                self._settings,
                user_id,
                asset_id,
                expected_role="product_image",
            )
            if resolved.available:
                images.append(resolved)
        return images

    async def _visible_product_id(self, tenant: Any, user_id: str, token: str) -> str | None:
        if " " in token or len(token) > 64:
            return await self._product_id_by_name(tenant, token)
        payload = await self._mcp_data(tenant, "get_product_image", {"product_id": token})
        if payload.get("found"):
            return token
        probed = resolve_product_assets(self._session, self._settings, user_id, token)
        if probed and probed[0].state is AssetState.UNAUTHORIZED:
            return None
        if probed and probed[0].state is not AssetState.MISSING:
            return token
        return await self._product_id_by_name(tenant, token)

    async def _product_id_by_name(self, tenant: Any, token: str) -> str | None:
        product = await self._mcp_data(tenant, "get_product", {"name": token})
        if not product.get("found"):
            return None
        body = product.get("product")
        if isinstance(body, dict) and body.get("id"):
            return str(body["id"])
        return None

    async def _product_image_payload(self, tenant: Any, token: str) -> dict[str, Any]:
        if " " not in token and len(token) <= 64:
            by_id = await self._mcp_data(tenant, "get_product_image", {"product_id": token})
            if by_id.get("found"):
                return by_id
        product = await self._mcp_data(tenant, "get_product", {"name": token})
        if not product.get("found"):
            return {"found": False}
        return await self._mcp_data(tenant, "get_product_image", {"product": token})

    async def _mcp_data(self, tenant: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._mcp.invoke(name, arguments, tenant)
        except MCPError:
            return {"found": False}
        data = getattr(result, "data", None)
        return data if isinstance(data, dict) else {"found": False}


def _featured_tokens(plan: ContentPlan) -> list[str]:
    tokens: list[str] = []
    featured = str(plan.featured_product_or_service or "").strip()
    if featured:
        tokens.append(featured)
    for item in plan.product_ids:
        text = str(item).strip()
        if text and text not in tokens:
            tokens.append(text)
    return tokens[:8]


def build_content_agent(llm: Any, image_generator: Any, **kwargs: Any) -> ContentAgent:
    return ContentAgent(llm, image_generator, **kwargs)
