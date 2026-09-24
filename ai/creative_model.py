"""Creative planner for the studio path.

LLM_PROVIDER=deepseek with a key uses DeepSeek. An OpenAI key does not select
a reasoning model. Without a DeepSeek key the local planner is used so startup
and tests do not call the network.
"""

from __future__ import annotations

import re
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from config import Settings
from models.creative import (
    CampaignType,
    CanvaAction,
    CreativeContext,
    CreativePlan,
    ImageQAVerdict,
    SourcedProduct,
)


def _hashtag(name: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]", "", name)
    return f"#{token[:40]}" if token else "#Business"


def _selected_products(context: CreativeContext) -> list[SourcedProduct]:
    return list(context.products[:4])


class GroundedCreativeModel:
    """Deterministic planner and structural image check. No network calls."""

    async def create_plan(self, context: CreativeContext, *, feedback: str | None = None) -> CreativePlan:
        del feedback
        products = _selected_products(context)
        names = [product.name for product in products] or [context.business.name]
        subject = ", ".join(names)
        festival = context.festival.name if context.festival else None
        style = context.brand.style or "clean brand-consistent"
        caption_parts = [context.business.name, subject]
        if festival:
            caption_parts.insert(1, festival)
        offer_text = next((product.offer for product in products if product.offer), None)
        if offer_text is None and context.offers:
            offer_text = context.offers[0].text
        if offer_text:
            caption_parts.append(offer_text)
        direction = style
        prompt = (
            f"Photorealistic Instagram still image of {context.business.name}. "
            f"Show only: {subject}. Style: {style}. No overlaid text."
        )
        if festival:
            prompt = (
                f"Photorealistic Instagram still image of {context.business.name} for {festival}. "
                f"Show only: {subject}. Style: {style}. No overlaid text."
            )
        asset_ids: list[str] = []
        action = CanvaAction.NONE
        if context.canva.queried and context.canva.asset_ids and context.canva.action != CanvaAction.NONE:
            action = context.canva.action
            asset_ids = list(context.canva.asset_ids[:4])
        elif context.brand.asset_ids:
            asset_ids = list(context.brand.asset_ids[:1])
        requirements = [
            "The image is a readable still.",
            "Only the listed products appear.",
            "No unlisted prices, offers, or dates are shown.",
        ]
        if festival:
            requirements.append("The festival name matches the supplied festival.")
        return CreativePlan(
            campaign_type=context.campaign_type,
            festival=festival,
            business_type=context.business.business_type,
            audience=context.brand.audience,
            caption=". ".join(part for part in caption_parts if part),
            hashtags=[_hashtag(context.business.name)],
            creative_direction=direction,
            image_prompt=prompt,
            product_ids=[product.id for product in products],
            asset_ids=asset_ids,
            canva_action=action,
            qa_requirements=requirements,
        )

    async def review_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        plan: CreativePlan,
        requirements: list[str],
    ) -> ImageQAVerdict:
        del mime_type, plan
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError):
            return ImageQAVerdict(passed=False, issues=["The image could not be read."])
        if width < 64 or height < 64:
            return ImageQAVerdict(passed=False, issues=["The image is too small."])
        return ImageQAVerdict(passed=True, issues=[], )


def get_creative_model(settings: Settings):
    from ai.llm_client import reasoning_provider_name

    if reasoning_provider_name(settings) == "deepseek" and settings.deepseek_api_key.strip():
        from ai.llm.deepseek import DeepSeekCreativeClient

        return DeepSeekCreativeClient(settings)
    return GroundedCreativeModel()
