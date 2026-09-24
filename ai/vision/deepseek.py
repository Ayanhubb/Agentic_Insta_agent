"""DeepSeek vision provider.

Uses the official DeepSeek chat API. deepseek-flash understands images.
Local files and private storage are sent as base64 only because the API cannot
read them. Public HTTPS URLs are passed through without base64.

The provider never publishes to Instagram and never returns the API key.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai.llm.deepseek import JSON_SYSTEM, DeepSeekMessage, DeepSeekSession
from config import Settings
from models.errors import AppError, ErrorCode
from services.logging import log_step
from services.media_paths import (
    INTERNAL_FILENAME_RE,
    STORAGE_STAGES,
    MediaPaths,
    assert_safe_user_id,
    is_within,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PUBLIC_URL_LENGTH = 8192
BLOCKED_IMAGE_HOSTS = frozenset({"localhost", "metadata.google.internal"})

UNDERSTANDING_EXAMPLE = {
    "summary": "What the image shows.",
    "objects": ["product"],
    "visible_text": [],
    "brand_cues": [],
    "quality_notes": ["sharp still"],
    "suitable_for_instagram": True,
}
QA_EXAMPLE = {"answer": "A direct answer.", "observations": ["what is visible"]}
REVIEW_EXAMPLE = {
    "passed": True,
    "score": 0.9,
    "reasons": ["The still matches the business brief."],
    "violations": [],
    "requires_regenerate": False,
}


class VisionImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    local_path: str | None = None
    storage_ref: str | None = None
    public_url: str | None = None
    image_base64: str | None = None
    detail: Literal["low", "high", "original", "auto"] = "auto"


class ImageUnderstanding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    summary: str
    objects: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    brand_cues: list[str] = Field(default_factory=list)
    quality_notes: list[str] = Field(default_factory=list)
    suitable_for_instagram: bool
    request_id: str | None = None

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("summary is required")
        return text


class ImageAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    question: str
    answer: str
    observations: list[str] = Field(default_factory=list)
    request_id: str | None = None

    @field_validator("answer")
    @classmethod
    def _answer(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("answer is required")
        return text


class VisionQaResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    passed: bool
    score: float
    reasons: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    requires_regenerate: bool = False
    request_id: str | None = None

    @field_validator("score")
    @classmethod
    def _score(cls, value: float) -> float:
        score = float(value)
        if score < 0 or score > 1:
            raise ValueError("score must be between 0 and 1")
        return score


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, "Media file was not found.", http_status=404)


def _invalid_image(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_IMAGE_URL, message, http_status=400)


def _invalid_response(message: str) -> AppError:
    return AppError(ErrorCode.DEEPSEEK_INVALID_RESPONSE, message, http_status=502, retryable=True)


def sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise AppError(
        ErrorCode.UNSUPPORTED_FORMAT,
        "Only JPEG, PNG, GIF, and WebP images are supported.",
        http_status=400,
    )


def _check_image_bytes(data: bytes) -> str:
    if not data:
        raise AppError(ErrorCode.INVALID_FILE, "The image was empty.", http_status=400)
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(ErrorCode.FILE_TOO_LARGE, "The image exceeds the DeepSeek size limit.", http_status=413)
    return sniff_image_mime(data)


def is_appropriate_public_url(url: str) -> bool:
    text = (url or "").strip()
    if not text or len(text) > MAX_PUBLIC_URL_LENGTH or text.lower().startswith("data:"):
        return False
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in BLOCKED_IMAGE_HOSTS or host.endswith((".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return False
    return True


def _read_storage_bytes(settings: Settings, user_id: str, storage_ref: str) -> bytes:
    owner = assert_safe_user_id(user_id)
    paths = MediaPaths(settings.media_root)
    try:
        resolved = paths.from_relative(storage_ref)
        relative = paths.relative_to_root(resolved)
    except AppError as exc:
        raise _not_found() from exc
    parts = relative.split("/")
    if len(parts) != 3 or parts[0] not in STORAGE_STAGES or parts[1] != owner:
        raise _not_found()
    if not INTERNAL_FILENAME_RE.fullmatch(parts[2]) or not resolved.is_file():
        raise _not_found()
    return resolved.read_bytes()


def _read_local_bytes(settings: Settings, user_id: str, raw_path: str) -> bytes:
    owner = assert_safe_user_id(user_id)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (Path(settings.media_root) / candidate).resolve()
    else:
        candidate = candidate.resolve()
    roots = [Path(settings.media_root), Path(settings.temp_dir), Path(settings.input_dir)]
    if not any(is_within(candidate, Path(root).resolve()) for root in roots):
        raise _not_found()
    if owner not in candidate.parts:
        raise _not_found()
    media_root = Path(settings.media_root).resolve()
    if is_within(candidate, media_root):
        relative = candidate.relative_to(media_root)
        if relative.parts and relative.parts[0] in STORAGE_STAGES:
            if len(relative.parts) < 2 or relative.parts[1] != owner:
                raise _not_found()
    if not candidate.is_file():
        raise _not_found()
    return candidate.read_bytes()


def _decode_base64(value: str) -> bytes:
    raw = (value or "").strip()
    if raw.lower().startswith("data:"):
        header, _, payload = raw.partition(",")
        if "base64" not in header.lower() or not payload:
            raise _invalid_image("The image data URL is not valid base64.")
        raw = payload
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise _invalid_image("The image data was not valid base64.") from exc


def image_content_part(settings: Settings, image: VisionImage) -> dict[str, Any]:
    """Public HTTPS URLs stay URLs. Everything else is base64 because the API cannot fetch it."""
    detail = image.detail
    if image.public_url:
        url = image.public_url.strip()
        if not is_appropriate_public_url(url):
            raise _invalid_image("That image URL is not a public HTTPS URL.")
        return {"type": "image_url", "image_url": {"url": url, "detail": detail}}
    if image.storage_ref:
        data = _read_storage_bytes(settings, image.user_id, image.storage_ref)
    elif image.local_path:
        data = _read_local_bytes(settings, image.user_id, image.local_path)
    elif image.image_base64:
        data = _decode_base64(image.image_base64)
    else:
        raise AppError(ErrorCode.INVALID_FILE, "An image is required.", http_status=400)
    mime = _check_image_bytes(data)
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": detail}}


class DeepSeekVisionProvider:
    """Image understanding and image QA. Does not generate or publish images."""

    provider_id = "deepseek"

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        transport: Any | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        chosen = transport if transport is not None else client
        if chosen is not None and not hasattr(chosen, "chat"):
            chosen = None
        self._settings = settings
        self._session = DeepSeekSession(settings, transport=chosen, sleeper=sleeper, vision=True)

    async def understand_image(self, image: VisionImage, *, prompt: str | None = None) -> ImageUnderstanding:
        instruction = prompt.strip() if prompt and prompt.strip() else "Describe this image for an Instagram still."
        message, data = await self._vision_json(image, instruction=instruction, example=UNDERSTANDING_EXAMPLE, task="image_understanding")
        try:
            result = ImageUnderstanding.model_validate(
                {**data, "provider": "deepseek", "model": message.model, "request_id": message.request_id}
            )
        except (ValidationError, ValueError) as exc:
            raise _invalid_response("The DeepSeek vision response was not valid image understanding.") from exc
        self._log("understand_image", message)
        return result

    async def answer_image_question(self, image: VisionImage, question: str) -> ImageAnswer:
        asked = (question or "").strip()
        if not asked:
            raise AppError(ErrorCode.INVALID_REQUEST, "A question is required.", http_status=400)
        message, data = await self._vision_json(image, instruction=asked, example=QA_EXAMPLE, task="image_qa")
        try:
            result = ImageAnswer.model_validate(
                {**data, "question": asked, "provider": "deepseek", "model": message.model, "request_id": message.request_id}
            )
        except (ValidationError, ValueError) as exc:
            raise _invalid_response("The DeepSeek vision response was not a valid answer.") from exc
        self._log("answer_image_question", message)
        return result

    async def review_image(
        self,
        image: VisionImage | None = None,
        *,
        brief: str | None = None,
        image_path: str | None = None,
        image_bytes: bytes | None = None,
        prompt: str = "",
        user_id: str = "",
        **_: Any,
    ) -> VisionQaResult | dict[str, Any]:
        """Typed QA for VisionImage, or a dict for scheduler keyword calls."""
        if isinstance(image, VisionImage):
            context = (brief or "").strip()
            if not context:
                raise AppError(ErrorCode.INVALID_REQUEST, "An image QA brief is required.", http_status=400)
            return await self._review_typed(image, context)
        owner = (user_id or "").strip()
        if image_bytes:
            vision_image = VisionImage(user_id=owner or "creative-review", image_base64=base64.b64encode(image_bytes).decode("ascii"))
        elif image_path:
            vision_image = VisionImage(user_id=owner or "unknown", local_path=image_path)
        else:
            raise AppError(ErrorCode.INVALID_FILE, "An image is required.", http_status=400)
        result = await self._review_typed(vision_image, (brief or prompt or "Review this still image for Instagram.").strip())
        return {
            "passed": result.passed,
            "score": result.score,
            "reasons": result.reasons,
            "violations": result.violations,
            "requires_regenerate": result.requires_regenerate,
            "malformed": False,
        }

    async def _review_typed(self, image: VisionImage, brief: str) -> VisionQaResult:
        message, data = await self._vision_json(image, instruction=brief, example=REVIEW_EXAMPLE, task="image_qa_review")
        try:
            result = VisionQaResult.model_validate(
                {**data, "provider": "deepseek", "model": message.model, "request_id": message.request_id}
            )
        except (ValidationError, ValueError) as exc:
            raise _invalid_response("The DeepSeek vision response was not a valid QA result.") from exc
        if result.violations and result.passed:
            result = result.model_copy(update={"passed": False, "requires_regenerate": True})
        self._log("review_image", message)
        return result

    async def _vision_json(
        self,
        image: VisionImage,
        *,
        instruction: str,
        example: dict[str, Any],
        task: str,
    ) -> tuple[DeepSeekMessage, dict[str, Any]]:
        part = image_content_part(self._settings, image)
        return await self._session.complete_json(
            [
                {
                    "role": "system",
                    "content": JSON_SYSTEM + "\nDescribe only what is visible. Do not invent prices, offers, or logos.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps({"task": task, "instruction": instruction, "json_example": example})},
                        part,
                    ],
                },
            ],
            user_id=image.user_id,
        )

    def _log(self, step: str, message: DeepSeekMessage) -> None:
        log_step(
            logger,
            event="DEEPSEEK_VISION_COMPLETED",
            task_id="deepseek-vision",
            step=step,
            tool="DeepSeekVisionProvider",
            status="success",
            model=message.model,
            provider="deepseek",
            request_id=message.request_id,
        )


def get_vision_provider(
    settings: Settings,
    *,
    client: object | None = None,
    transport: object | None = None,
) -> DeepSeekVisionProvider | None:
    name = (getattr(settings, "vision_provider", "") or "deepseek").strip().lower()
    if name not in {"", "deepseek"}:
        raise AppError(
            ErrorCode.DEEPSEEK_CONFIGURATION_ERROR,
            "The configured vision provider is not supported.",
            http_status=503,
            details={"vision_provider": name},
        )
    if not settings.deepseek_api_key.strip():
        return None
    return DeepSeekVisionProvider(settings, client=client, transport=transport)
