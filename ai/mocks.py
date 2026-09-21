"""Test doubles for OpenAI. Default pytest never makes real OpenAI requests."""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from PIL import Image

from ai.image_generator import ImageGenerationProvider
from ai.llm_client import LLMProvider
from ai.schemas import ContentPlan, ContentPlanRequest, GeneratedImage, GenerationStatus, ImageGenerationRequest
from models.errors import AppError, ErrorCode
from services.media_storage import MediaStorage


def default_content_plan(request: ContentPlanRequest | None = None) -> ContentPlan:
    reason = "user_prompt"
    content_type = "product"
    theme = "handcrafted display"
    unique = uuid4().hex[:8]
    festival_name = ""
    if request is not None:
        hint = (request.reason_hint or "").lower()
        if request.festival:
            festival_name = (
                getattr(request.festival, "festival_name", None)
                or getattr(request.festival, "name", None)
                or ""
            ).strip()
        if "festival" in hint or festival_name:
            reason = "festival_campaign"
            content_type = "festival"
            theme = f"{festival_name or 'festival'} collection {unique}"
        elif "daily" in hint:
            reason = "daily_automation"
            content_type = "product"
            theme = f"handcrafted display {unique}"
    business = "Local retail brand"
    if request and request.business_profile and request.business_profile.business_name:
        business = request.business_profile.business_name
    prompt = (
        f"Photorealistic Instagram square photo of {business} products on a clean studio "
        f"table, natural window light, brand-consistent colors, no text overlay. Shot {unique}."
    )
    if festival_name:
        prompt = (
            f"Photorealistic Instagram photo of {business} {festival_name} collection, "
            f"festive {festival_name} styling with products in natural light, variation {unique}, no text."
        )
    if request and request.user_prompt:
        prompt = (
            f"Photorealistic Instagram photo inspired by: {request.user_prompt}. "
            f"Show {business} products with natural lighting and no text. Shot {unique}."
        )
    return ContentPlan(
        content_type=content_type,
        theme=theme,
        image_prompt=prompt,
        business_context=f"{business} retail content for Instagram.",
        reason=reason,
    )


class MockLLMProvider(LLMProvider):
    def __init__(
        self,
        plan: ContentPlan | None = None,
        *,
        error: AppError | None = None,
        raw_invalid: bool = False,
    ) -> None:
        self.plan = plan
        self.error = error
        self.raw_invalid = raw_invalid
        self.calls = 0
        self.requests: list[ContentPlanRequest] = []

    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        self.calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.raw_invalid:
            raise AppError(
                ErrorCode.OPENAI_INVALID_RESPONSE,
                "The OpenAI response was not a valid content plan.",
                http_status=502,
            )
        return self.plan or default_content_plan(request)


class MockImageGenerationProvider(ImageGenerationProvider):
    def __init__(self, storage: MediaStorage, *, error: AppError | None = None, color: tuple[int, int, int] = (180, 40, 50)) -> None:
        self._storage = storage
        self.error = error
        self.calls = 0
        self.color = color

    async def generate(self, request: ImageGenerationRequest | None = None, **kwargs) -> GeneratedImage:
        if request is None:
            request = ImageGenerationRequest(
                prompt=str(kwargs.get("prompt") or ""),
                user_id=str(kwargs.get("user_id") or ""),
                original_prompt=kwargs.get("original_prompt"),
                source=kwargs.get("source") or "USER_PROMPT",  # type: ignore[arg-type]
            )
        self.calls += 1
        if self.error is not None:
            raise self.error
        buffer = BytesIO()
        Image.new("RGB", (1024, 1024), self.color).save(buffer, format="JPEG", quality=90)
        path = self._storage.save_bytes(request.user_id, "generated", buffer.getvalue(), "image/jpeg")
        return GeneratedImage(
            id=str(uuid4()),
            user_id=request.user_id,
            original_prompt=request.original_prompt or request.prompt,
            enhanced_prompt=request.prompt,
            model="mock-image",
            provider="mock",
            filename=path.name,
            storage_path=str(path),
            mime_type="image/jpeg",
            width=1024,
            height=1024,
            generation_status=GenerationStatus.GENERATED,
            source=request.source,
        )
