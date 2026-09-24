"""Image QA gate. DeepSeek Vision is used when a vision client is configured."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ImageQAResult:
    passed: bool
    malformed: bool = False
    reasons: list[str] = field(default_factory=list)
    provider: str = "structural"


def _image_id(image: Any) -> str | None:
    value = getattr(image, "id", None)
    if value is None and isinstance(image, dict):
        value = image.get("id")
    text = str(value or "").strip()
    return text or None


def _storage_path(image: Any) -> str | None:
    value = getattr(image, "storage_path", None) or getattr(image, "filename", None)
    if value is None and isinstance(image, dict):
        value = image.get("storage_path") or image.get("filename")
    text = str(value or "").strip()
    return text or None


class ImageQAService:
    def __init__(self, vision: Any | None = None) -> None:
        self._vision = vision

    async def review(
        self,
        *,
        image: Any | None,
        prompt: str | None,
        user_id: str,
        correlation_id: str,
    ) -> ImageQAResult:
        if image is None or not _image_id(image) or not _storage_path(image):
            return ImageQAResult(
                passed=False,
                malformed=True,
                reasons=["provider_result_malformed"],
                provider="structural",
            )
        if self._vision is None:
            return ImageQAResult(passed=True, provider="structural")

        raw = await self._call_vision(
            image_path=_storage_path(image),
            image_id=_image_id(image),
            prompt=prompt or "",
            user_id=user_id,
            correlation_id=correlation_id,
        )
        if not isinstance(raw, dict) or "passed" not in raw:
            return ImageQAResult(
                passed=False,
                malformed=True,
                reasons=["provider_result_malformed"],
                provider="deepseek_vision",
            )
        passed = bool(raw.get("passed"))
        reasons = raw.get("reasons") if isinstance(raw.get("reasons"), list) else []
        safe_reasons = [str(item)[:120] for item in reasons if item]
        if not passed:
            return ImageQAResult(
                passed=False,
                reasons=safe_reasons or ["image_qa_failed"],
                provider="deepseek_vision",
            )
        return ImageQAResult(passed=True, provider="deepseek_vision")

    async def _call_vision(self, **kwargs: Any) -> Any:
        for name in ("review_image", "qa_image", "analyze_image"):
            method = getattr(self._vision, name, None)
            if not callable(method):
                continue
            try:
                result = method(**kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result
            except TypeError:
                continue
            except Exception:
                logger.warning("Vision QA provider failed", extra={"step": "IMAGE_QA"})
                return {"malformed": True}
        return {"malformed": True}
