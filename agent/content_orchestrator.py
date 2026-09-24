"""Content Agent orchestration.

Authenticate, load business and campaign facts from MCP, ask DeepSeek for a
CreativePlan, generate an image, run vision QA, and hand the asset to approval.

This module does not publish and does not call the Instagram Agent.
"""

from __future__ import annotations

import inspect
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.creative_validation import reject_untrusted_plan, validate_creative_plan
from ai.schemas import ContentSource as ImageSource
from ai.schemas import ImageGenerationRequest, ImageReference
from backend.ai.image.base import CONTENT_AGENT_CALLER, ImageInput, ImageRequest
from backend.mcp.client import MCPClient
from backend.mcp.errors import MCPError
from backend.mcp.servers import build_registry
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import trusted_tenant
from models.content import (
    ApprovalStatus,
    ContentMode,
    ContentPlan,
    ContentSource,
    ContentStrategyResult,
    ContentTask,
    ContentTaskStatus,
    ContentType,
    DiversityVerdict,
    InstagramHandoff,
)
from models.creative import (
    BrandFact,
    BusinessFact,
    CampaignType,
    CanvaAction,
    CanvaFact,
    ContentOrchestrationRequest,
    CreativeContext,
    CreativePlan,
    ImageQAVerdict,
    SourcedFestival,
    SourcedOffer,
    SourcedProduct,
)
from models.errors import AppError, ErrorCode
from services.logging import log_step

logger = logging.getLogger(__name__)

PLAN_ATTEMPT_CAP = 3
QA_ATTEMPT_CAP = 3


class ContentOrchestrationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategy: ContentStrategyResult
    creative_plan: CreativePlan
    qa: ImageQAVerdict
    plan_attempts: int
    qa_attempts: int
    published: Literal[False] = False


class DisabledCanva:
    """Canva is queried only when requested. With no connection the action is none."""

    async def query(self, *, user_id: str) -> dict[str, Any]:
        del user_id
        return {"queried": True, "action": "none", "asset_ids": []}


def build_content_mcp(session_factory: Any, clock: Any | None = None) -> MCPClient:
    gateway = RepositoryGateway(session_factory, clock)
    return MCPClient(build_registry(gateway))


def _bound(value: int, cap: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(cap, parsed))


def _product_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "item"
    return f"product:{slug}"[:80]


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _mode(value: str | ContentMode) -> ContentMode:
    if isinstance(value, ContentMode):
        return value
    try:
        return ContentMode(str(value).strip().upper())
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "The content mode is not supported.") from exc


def _mentions(name: str, prompt: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(name.casefold())}(?![a-z0-9])", prompt.casefold()) is not None


class ContentOrchestrator:
    def __init__(
        self,
        creative: Any,
        images: Any,
        mcp: Any,
        *,
        store: Any | None = None,
        session: Any | None = None,
        settings: Any | None = None,
        canva: Any | None = None,
        clock: Any | None = None,
        max_plan_attempts: int = 2,
        max_qa_attempts: int = 2,
    ) -> None:
        self._creative = creative
        self._images = images
        self._mcp = mcp
        self._store = store
        self._session = session
        self._settings = settings
        self._canva = canva or DisabledCanva()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._plan_attempts = _bound(max_plan_attempts, PLAN_ATTEMPT_CAP)
        self._qa_attempts = _bound(max_qa_attempts, QA_ATTEMPT_CAP)

    async def run(self, request: ContentOrchestrationRequest) -> ContentOrchestrationResult:
        tenant = self._authenticate(request)
        mode = _mode(request.mode)
        now = self._clock()
        if mode == ContentMode.USER_PROMPT and not (request.user_prompt or "").strip():
            raise AppError(ErrorCode.INVALID_REQUEST, "A prompt is required.")

        business_payload = await self._tool(tenant, "get_business_profile")
        profile = business_payload.get("profile") if business_payload.get("found") else None
        if not isinstance(profile, dict) or not str(profile.get("business_name") or "").strip():
            raise AppError(ErrorCode.CONTENT_PROFILE_MISSING, "A business profile is required.")
        business = BusinessFact(
            name=str(profile["business_name"]).strip(),
            business_type=profile.get("business_type"),
            category=profile.get("business_category"),
            description=profile.get("description"),
            location=profile.get("location"),
            language=profile.get("preferred_language"),
        )

        products = await self._products(tenant, request, _names(profile.get("products")))
        offers = await self._offers(tenant, products, request.offer_id)
        brand = await self._brand(tenant)
        festival, campaign_type = await self._campaign(tenant, request, mode, (request.user_prompt or ""))
        canva = CanvaFact()
        if _canva_requested(request, request.user_prompt or ""):
            canva = _canva_fact(await self._call_canva(tenant.tenant_id))

        context = CreativeContext(
            user_id=tenant.tenant_id,
            campaign_type=campaign_type,
            business=business,
            brand=brand,
            products=products,
            offers=offers,
            festival=festival,
            canva=canva,
            user_prompt=(request.user_prompt or "").strip() or None,
            current_date=now.date(),
        )
        plan, plan_attempts = await self._plan(context)
        generated, qa, qa_attempts = await self._image_with_qa(request, context, plan, mode)
        if self._store and hasattr(self._store, "save_generated_image"):
            generated = await self._store.save_generated_image(generated)

        source = _source(mode)
        approval = ApprovalStatus.PENDING_APPROVAL
        content_plan = _legacy_plan(plan, business.name)
        task = ContentTask(
            id=request.task_id or str(uuid4()),
            user_id=tenant.tenant_id,
            mode=mode,
            source=source,
            status=ContentTaskStatus.AWAITING_APPROVAL,
            plan=content_plan,
            generated_image_id=generated.id,
            approval_status=approval,
            requires_approval=True,
            handoff_to_instagram=False,
            published=False,
        )
        if self._store and hasattr(self._store, "save_content_task"):
            await self._store.save_content_task(task)
        caption = plan.caption
        if plan.hashtags:
            caption = f"{caption}\n{' '.join(plan.hashtags)}"
        handoff = InstagramHandoff(
            ready=False,
            user_id=tenant.tenant_id,
            generated_image_id=generated.id,
            image_path=generated.storage_path,
            caption=caption,
            source=source.value,
            content_type=content_plan.content_type.value,
            instagram_account_id=request.instagram_account_id,
            requires_instagram_agent=True,
            auto_approved=False,
        )
        strategy = ContentStrategyResult(
            task=task,
            plan=content_plan,
            generated_image=generated,
            approval_status=approval,
            diversity=DiversityVerdict(accepted=True, reason="creative_plan_validated", blocking=False),
            handoff=handoff,
            published=False,
            llm_attempts=plan_attempts,
            mode=mode,
            source=source,
        )
        log_step(logger, event="CONTENT_PLAN_CREATED", task_id=task.id, status="success", mode=mode.value)
        return ContentOrchestrationResult(
            strategy=strategy,
            creative_plan=plan,
            qa=qa,
            plan_attempts=plan_attempts,
            qa_attempts=qa_attempts,
            published=False,
        )

    def _authenticate(self, request: ContentOrchestrationRequest):
        if not request.authenticated:
            raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication is required.", http_status=401)
        try:
            return trusted_tenant(request.user_id, source="authenticated")
        except MCPError as exc:
            raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication is required.", http_status=401) from exc

    async def _tool(self, tenant: Any, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = await self._mcp.invoke(name, arguments or {}, tenant)
        except MCPError as exc:
            raise AppError(ErrorCode.CONTENT_LLM_FAILED, "Context could not be loaded.", http_status=502) from exc
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            raise AppError(ErrorCode.CONTENT_LLM_FAILED, "Context could not be loaded.", http_status=502)
        return data

    async def _products(
        self,
        tenant: Any,
        request: ContentOrchestrationRequest,
        profile_names: list[str],
    ) -> list[SourcedProduct]:
        catalog = self._catalog_products(tenant.tenant_id, request.product_id)
        if request.product_id and not catalog:
            raise AppError(ErrorCode.INVALID_REQUEST, "The product was not found.")
        names = [item.name for item in catalog] or profile_names
        confirmed: list[str] = []
        for name in names[:8]:
            payload = await self._tool(tenant, "get_product", {"name": name})
            product = payload.get("product") if payload.get("found") else None
            if isinstance(product, dict) and product.get("name"):
                confirmed.append(str(product["name"]))
            elif catalog:
                confirmed.append(name)
        if catalog:
            by_name = {item.name.casefold(): item for item in catalog}
            selected = []
            for name in confirmed:
                row = by_name.get(name.casefold())
                if row is not None:
                    selected.append(row)
            return selected or catalog
        return [SourcedProduct(id=_product_id(name), name=name) for name in confirmed]

    def _catalog_products(self, user_id: str, product_id: str | None) -> list[SourcedProduct]:
        if self._session is None:
            return []
        from db.asset_repositories import ProductAssetRepository, ProductRepository

        repo = ProductRepository(self._session)
        links = ProductAssetRepository(self._session)
        if product_id:
            rows = [row for row in [repo.get_owned(user_id, product_id)] if row is not None]
        else:
            rows = repo.list_for_user(user_id, active_only=True)
        products: list[SourcedProduct] = []
        for row in rows:
            link = links.primary_for_product(user_id, row.id)
            price = None if row.price is None else format(row.price, "f").rstrip("0").rstrip(".")
            products.append(
                SourcedProduct(
                    id=row.id,
                    name=row.name,
                    description=row.description,
                    price=price or None,
                    offer=(row.offer or "").strip() or None,
                    asset_id=link.asset_id if link is not None else None,
                )
            )
        return products

    async def _offers(self, tenant: Any, products: list[SourcedProduct], offer_id: str | None) -> list[SourcedOffer]:
        payload = await self._tool(tenant, "get_active_offers")
        offers: list[SourcedOffer] = []
        for index, item in enumerate(payload.get("offers") or []):
            if not isinstance(item, dict):
                continue
            text = item.get("offer") or item.get("title") or item.get("price_text")
            price = item.get("price") or item.get("price_text")
            if not text and not price:
                continue
            offers.append(
                SourcedOffer(
                    id=str(item.get("id") or f"offer:{index}"),
                    text=str(text or price),
                    price=str(price) if price else None,
                )
            )
        for product in products:
            if product.offer:
                offers.append(SourcedOffer(id=f"offer:{product.id}", text=product.offer, product_id=product.id, price=product.price))
        if offer_id:
            match = next((item for item in offers if item.id == offer_id or item.product_id == offer_id), None)
            if match is None:
                raise AppError(ErrorCode.INVALID_REQUEST, "The offer was not found.")
            return [match]
        return offers

    async def _brand(self, tenant: Any) -> BrandFact:
        payload = await self._tool(tenant, "get_brand_guidelines")
        guidelines = payload.get("guidelines") if payload.get("found") else None
        if not isinstance(guidelines, dict):
            guidelines = {}
        logo = await self._tool(tenant, "get_company_logo")
        asset = logo.get("asset") if logo.get("found") else None
        asset_ids = []
        if isinstance(asset, dict) and asset.get("id"):
            asset_ids.append(str(asset["id"]))
        return BrandFact(
            audience=guidelines.get("target_audience"),
            style=guidelines.get("brand_style"),
            language=guidelines.get("preferred_language"),
            asset_ids=asset_ids,
        )

    async def _campaign(
        self,
        tenant: Any,
        request: ContentOrchestrationRequest,
        mode: ContentMode,
        prompt: str,
    ) -> tuple[SourcedFestival | None, CampaignType]:
        upcoming = await self._tool(tenant, "get_upcoming_festivals", {"within_days": 60})
        names = [
            str(item["festival_name"])
            for item in upcoming.get("festivals") or []
            if isinstance(item, dict) and item.get("festival_name")
        ]
        chosen: str | None = None
        explicit = (request.festival or "").strip()
        if explicit:
            chosen = explicit
        elif mode == ContentMode.FESTIVAL:
            chosen = names[0] if names else None
        else:
            for name in sorted(set(names), key=len, reverse=True):
                if _mentions(name, prompt):
                    chosen = name
                    break
        if mode == ContentMode.DAILY and not explicit:
            return None, CampaignType.DAILY
        if not chosen:
            if mode == ContentMode.FESTIVAL:
                raise AppError(ErrorCode.INVALID_REQUEST, "Festival mode requires a sourced festival.")
            return None, CampaignType.USER_PROMPT
        details = await self._tool(tenant, "get_festival_details", {"festival_name": chosen})
        festival = details.get("festival") if details.get("found") else None
        if not isinstance(festival, dict) or not festival.get("festival_name"):
            raise AppError(ErrorCode.INVALID_REQUEST, "The festival was not found.")
        sourced = SourcedFestival(
            name=str(festival["festival_name"]),
            date=festival.get("date"),
            year=festival.get("year"),
            campaign_id=(festival.get("campaign") or {}).get("id") if isinstance(festival.get("campaign"), dict) else None,
        )
        if sourced.date is None and festival.get("date"):
            raise AppError(ErrorCode.INVALID_REQUEST, "The festival date was not supplied.")
        return sourced, CampaignType.FESTIVAL

    async def _call_canva(self, user_id: str) -> dict[str, Any]:
        query = getattr(self._canva, "query", None)
        if not callable(query):
            return {"queried": True, "action": "none", "asset_ids": []}
        result = query(user_id=user_id)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {"queried": True, "action": "none", "asset_ids": []}

    async def _plan(self, context: CreativeContext) -> tuple[CreativePlan, int]:
        feedback: str | None = None
        last_error: AppError | None = None
        for attempt in range(1, self._plan_attempts + 1):
            try:
                raw = await self._creative.create_plan(context, feedback=feedback)
                reject_untrusted_plan(raw)
                plan = raw if isinstance(raw, CreativePlan) else CreativePlan.model_validate(raw)
                validate_creative_plan(plan, context)
                return plan, attempt
            except ValidationError:
                last_error = AppError(ErrorCode.CONTENT_INVALID_PLAN, "The creative plan did not match the schema.")
                feedback = "Return only the required creative plan fields grounded in the supplied context."
            except AppError as exc:
                if exc.code == ErrorCode.CONTENT_POLICY_VIOLATION:
                    raise
                last_error = exc
                feedback = exc.message
        raise last_error or AppError(ErrorCode.CONTENT_INVALID_PLAN, "Could not create a creative plan.")

    async def _image_with_qa(
        self,
        request: ContentOrchestrationRequest,
        context: CreativeContext,
        plan: CreativePlan,
        mode: ContentMode,
    ):
        from models.content import GeneratedImageSnapshot

        qa = ImageQAVerdict(passed=False, issues=["Image QA did not run."])
        note: str | None = None
        for attempt in range(1, self._qa_attempts + 1):
            generated_raw = await self._generate(request, context, plan, mode, note)
            image_bytes = _image_bytes(generated_raw)
            qa = await self._creative.review_image(
                image_bytes=image_bytes,
                mime_type=getattr(generated_raw, "mime_type", None) or "image/png",
                plan=plan,
                requirements=plan.qa_requirements,
            )
            if not isinstance(qa, ImageQAVerdict):
                qa = ImageQAVerdict.model_validate(qa)
            if qa.passed:
                snapshot = _snapshot(generated_raw, request, plan, mode)
                return snapshot, qa, attempt
            note = "Regenerate the same scene. Do not add products, prices, offers, or dates."
        raise AppError(ErrorCode.CONTENT_QA_FAILED, "Image QA did not pass.", http_status=422)

    async def _generate(
        self,
        request: ContentOrchestrationRequest,
        context: CreativeContext,
        plan: CreativePlan,
        mode: ContentMode,
        note: str | None,
    ) -> Any:
        if self._images is None or not hasattr(self._images, "generate"):
            raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, "Image generation is not configured.")
        prompt = plan.image_prompt if not note else f"{plan.image_prompt}\n{note}"
        prompt = prompt[:4000]
        references = _image_references(plan, context)
        legacy = ImageGenerationRequest(
            prompt=prompt,
            user_id=request.user_id,
            original_prompt=request.user_prompt or plan.caption,
            source=_image_source(mode),
            references=references,
        )
        rich = ImageRequest(
            prompt=prompt,
            user_id=request.user_id,
            product_image=_first_input(self, request.user_id, [ref.asset_id for ref in references if ref.kind == "product"]),
            company_logo=_first_input(self, request.user_id, context.brand.asset_ids),
            reference_images=_inputs(self, request.user_id, plan.asset_ids),
        )
        generate = self._images.generate
        try:
            if "caller" in inspect.signature(generate).parameters:
                return await generate(rich, caller=CONTENT_AGENT_CALLER)
            return await generate(legacy)
        except AppError as exc:
            if exc.code == ErrorCode.CONTENT_IMAGE_FAILED:
                raise
            raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, exc.message, http_status=exc.http_status) from exc


def _canva_requested(request: ContentOrchestrationRequest, prompt: str) -> bool:
    if request.use_canva is False:
        return False
    if request.use_canva is True:
        return True
    return "canva" in prompt.casefold()


def _canva_fact(payload: dict[str, Any]) -> CanvaFact:
    action = str(payload.get("action") or "none").strip().lower()
    try:
        parsed = CanvaAction(action)
    except ValueError:
        parsed = CanvaAction.NONE
    asset_ids = [str(item).strip() for item in payload.get("asset_ids") or [] if str(item).strip()]
    if parsed != CanvaAction.NONE and not asset_ids:
        parsed = CanvaAction.NONE
    return CanvaFact(queried=True, action=parsed, asset_ids=asset_ids)


def _image_references(plan: CreativePlan, context: CreativeContext) -> list[ImageReference]:
    by_id = {product.id: product for product in context.products}
    refs: list[ImageReference] = []
    for product_id in plan.product_ids:
        product = by_id.get(product_id)
        if product is None:
            continue
        refs.append(ImageReference(asset_id=product.asset_id or product.id, kind="product", label=product.name))
    for asset_id in plan.asset_ids:
        refs.append(ImageReference(asset_id=asset_id, kind="asset", label="sourced asset"))
    return refs


def _first_input(orchestrator: ContentOrchestrator, user_id: str, asset_ids: list[str]) -> ImageInput | None:
    for asset_id in asset_ids:
        loaded = orchestrator._load_asset(user_id, asset_id)  # noqa: SLF001
        if loaded is not None:
            return loaded
    return None


def _inputs(orchestrator: ContentOrchestrator, user_id: str, asset_ids: list[str]) -> list[ImageInput]:
    loaded = []
    for asset_id in asset_ids:
        item = orchestrator._load_asset(user_id, asset_id)  # noqa: SLF001
        if item is not None:
            loaded.append(item)
    return loaded


def _image_source(mode: ContentMode) -> ImageSource:
    if mode == ContentMode.DAILY:
        return ImageSource.DAILY_AUTOMATION
    if mode == ContentMode.FESTIVAL:
        return ImageSource.FESTIVAL_AUTOMATION
    return ImageSource.USER_PROMPT


def _source(mode: ContentMode) -> ContentSource:
    return {
        ContentMode.USER_PROMPT: ContentSource.USER_PROMPT,
        ContentMode.DAILY: ContentSource.DAILY_AUTOMATION,
        ContentMode.FESTIVAL: ContentSource.FESTIVAL_AUTOMATION,
    }[mode]


def _legacy_plan(plan: CreativePlan, business_name: str) -> ContentPlan:
    content_type = ContentType.FESTIVAL if plan.campaign_type == CampaignType.FESTIVAL else ContentType.PRODUCT
    reason = {
        CampaignType.USER_PROMPT: "user prompt",
        CampaignType.DAILY: "daily product rotation",
        CampaignType.FESTIVAL: "festival campaign",
    }[plan.campaign_type]
    return ContentPlan(
        content_type=content_type,
        theme=plan.creative_direction[:120],
        image_prompt=plan.image_prompt,
        business_context=plan.business_type or business_name,
        reason=reason,
        caption_hint=plan.caption,
        featured_product_or_service=", ".join(plan.product_ids) or None,
        mode=None,
        source=None,
    )


def _image_bytes(result: Any) -> bytes:
    raw = getattr(result, "image_bytes", None)
    if isinstance(raw, (bytes, bytearray)) and raw:
        return bytes(raw)
    path = getattr(result, "storage_path", None)
    if path:
        from pathlib import Path

        file_path = Path(str(path))
        if file_path.is_file():
            return file_path.read_bytes()
    return b""


def _snapshot(result: Any, request: ContentOrchestrationRequest, plan: CreativePlan, mode: ContentMode):
    from models.content import GeneratedImageSnapshot

    content_type = ContentType.FESTIVAL if plan.campaign_type == CampaignType.FESTIVAL else ContentType.PRODUCT
    return GeneratedImageSnapshot(
        id=getattr(result, "id", None) or str(uuid4()),
        user_id=request.user_id,
        original_prompt=request.user_prompt or plan.caption,
        enhanced_prompt=getattr(result, "enhanced_prompt", None) or plan.image_prompt,
        model=getattr(result, "model", None),
        provider=getattr(result, "provider", None),
        filename=getattr(result, "filename", None),
        storage_path=getattr(result, "storage_path", None),
        mime_type=getattr(result, "mime_type", None) or "image/png",
        width=getattr(result, "width", None),
        height=getattr(result, "height", None),
        generation_status="GENERATED",
        approval_status=ApprovalStatus.PENDING_APPROVAL.value,
        source=_source(mode).value,
        content_type=content_type.value,
        theme=plan.creative_direction[:128],
    )


# Attached after the class so asset loading stays next to the image request builder.
def _load_asset(self: ContentOrchestrator, user_id: str, asset_id: str) -> ImageInput | None:
    if self._session is None or self._settings is None or not asset_id:
        return None
    from models.errors import AppError as AssetError
    from services.asset_catalog import AssetCatalog

    try:
        path, mime, filename = AssetCatalog(self._session, self._settings).open_media(user_id, asset_id)
        data = path.read_bytes()
    except (AssetError, OSError):
        return None
    if not data or mime.lower() not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return None
    return ImageInput(data=data, mime_type=mime, filename=filename)


ContentOrchestrator._load_asset = _load_asset  # type: ignore[method-assign]
