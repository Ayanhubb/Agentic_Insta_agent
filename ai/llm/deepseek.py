"""DeepSeek chat provider.

Official API: POST https://api.deepseek.com/chat/completions.
The API key is read from DEEPSEEK_API_KEY and is sent only as an Authorization
header. It is never logged, stored, or returned.

This provider plans and writes. It does not generate images, call Instagram,
or execute model output.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from httpx import AsyncClient as DeepSeekAsyncClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai.llm_client import LLMProvider
from ai.schemas import (
    ContentPlan,
    ContentPlanRequest,
    content_plan_json_schema,
    validate_image_prompt,
)
from config import Settings
from models.errors import AppError, ErrorCode
from services.logging import log_step, redact_text

logger = logging.getLogger(__name__)

DEEPSEEK_API_ROOT = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
ALLOWED_DEEPSEEK_HOSTS = frozenset({"api.deepseek.com", "localhost", "127.0.0.1"})
JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
HASHTAG_RE = re.compile(r"^[A-Za-z0-9_]{2,50}$")
MAX_CAPTION_LENGTH = 2200
MAX_HASHTAGS = 15

TEXT_SYSTEM = """You are the reasoning model for an Instagram business assistant.
Answer in plain text. You do not write code, shell commands, HTTP requests, database
queries, or Instagram API calls. You do not ask for API keys, OAuth tokens, or credentials.
You do not publish content.
"""

JSON_SYSTEM = """You are the planning model for an Instagram business assistant.
Return only a JSON object. The word JSON means the response format, not a tool.
You do not write code, shell commands, HTTP requests, database queries, or Instagram API calls.
You do not ask for API keys, OAuth tokens, or credentials. You do not publish content.
Do not include keys named command, token, tools, or url.
"""


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class TextReasoning(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    text: str
    reasoning: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    usage: TokenUsage | None = None


class CaptionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str
    business_name: str | None = None
    image_prompt: str | None = None
    language: str | None = None
    festival_name: str | None = None
    tone: str | None = None


class CaptionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    caption: str
    language: str | None = None
    request_id: str | None = None

    @field_validator("caption")
    @classmethod
    def _caption(cls, value: str) -> str:
        return _clean_caption(value)


class HashtagRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str
    business_name: str | None = None
    caption: str | None = None
    festival_name: str | None = None
    limit: int = 8


class HashtagResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    hashtags: list[str]
    request_id: str | None = None

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, value: list[str]) -> list[str]:
        return normalize_hashtags(value)


class CreativePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    content_type: str
    theme: str
    concept: str
    image_prompt: str
    caption: str
    hashtags: list[str]
    business_context: str
    reason: str
    festival_name: str | None = None
    festival_strategy: str | None = None
    request_id: str | None = None

    @field_validator("image_prompt")
    @classmethod
    def _image_prompt(cls, value: str) -> str:
        return validate_image_prompt(value)

    @field_validator("caption")
    @classmethod
    def _caption(cls, value: str) -> str:
        return _clean_caption(value)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, value: list[str]) -> list[str]:
        return normalize_hashtags(value)


class FestivalStrategy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["deepseek"] = "deepseek"
    model: str
    festival_name: str
    strategy: str
    post_ideas: list[str] = Field(default_factory=list)
    caption: str
    hashtags: list[str]
    image_direction: str
    business_context: str
    request_id: str | None = None

    @field_validator("caption")
    @classmethod
    def _caption(cls, value: str) -> str:
        return _clean_caption(value)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, value: list[str]) -> list[str]:
        return normalize_hashtags(value)

    @field_validator("image_direction")
    @classmethod
    def _direction(cls, value: str) -> str:
        return validate_image_prompt(value)


class DeepSeekMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str | None = None
    model: str
    text: str
    reasoning: str | None = None
    finish_reason: str | None = None
    usage: TokenUsage | None = None


def normalize_hashtags(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = str(raw).strip()
        if tag.startswith("#"):
            tag = tag[1:].strip()
        if not HASHTAG_RE.fullmatch(tag):
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(f"#{tag}")
        if len(cleaned) >= MAX_HASHTAGS:
            break
    if not cleaned:
        raise ValueError("hashtags are required")
    return cleaned


def _clean_caption(value: str) -> str:
    caption = (value or "").strip()
    if not caption:
        raise ValueError("caption is required")
    return caption[:MAX_CAPTION_LENGTH]


def extract_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty response")
    fenced = JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return parsed


def allowed_deepseek_base_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or host not in ALLOWED_DEEPSEEK_HOSTS:
        raise AppError(
            ErrorCode.DEEPSEEK_CONFIGURATION_ERROR,
            "The DeepSeek API base URL is not allowed.",
            http_status=503,
        )
    if host == "api.deepseek.com" and parsed.scheme != "https":
        raise AppError(
            ErrorCode.DEEPSEEK_CONFIGURATION_ERROR,
            "The DeepSeek API base URL is not allowed.",
            http_status=503,
        )
    if parsed.scheme not in {"https", "http"}:
        raise AppError(
            ErrorCode.DEEPSEEK_CONFIGURATION_ERROR,
            "The DeepSeek API base URL is not allowed.",
            http_status=503,
        )
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _model_name(settings: Settings) -> str:
    return (settings.deepseek_model or "").strip() or DEFAULT_DEEPSEEK_MODEL


def _timeout_seconds(settings: Settings) -> float:
    configured = float(getattr(settings, "deepseek_timeout_seconds", 0) or 0)
    if configured > 0:
        return configured
    return float(settings.request_timeout_seconds)


def _strip_secret(settings: Settings, text: str) -> str:
    key = (settings.deepseek_api_key or "").strip()
    if key and key in text:
        text = text.replace(key, "[REDACTED]")
    return redact_text(text)


def _configuration_error(message: str = "DeepSeek is not configured.") -> AppError:
    return AppError(ErrorCode.DEEPSEEK_CONFIGURATION_ERROR, message, http_status=503)


def _invalid(message: str, *, retryable: bool = True) -> AppError:
    return AppError(ErrorCode.DEEPSEEK_INVALID_RESPONSE, message, http_status=502, retryable=retryable)


def _status_error(status: int, *, vision: bool, retry_after: float | None) -> AppError:
    details = {"status_code": status}
    if status == 401:
        return _configuration_error("DeepSeek authentication failed.")
    if status == 429:
        return AppError(
            ErrorCode.DEEPSEEK_RATE_LIMITED,
            "The DeepSeek API rate limit was reached.",
            http_status=429,
            retryable=True,
            retry_after_seconds=retry_after,
            details=details,
        )
    if status in {402, 500, 503}:
        code = ErrorCode.VISION_PROVIDER_ERROR if vision else ErrorCode.DEEPSEEK_UNAVAILABLE
        message = (
            "The DeepSeek vision provider is unavailable."
            if vision
            else "The DeepSeek API is unavailable."
        )
        return AppError(
            code,
            message,
            http_status=503,
            retryable=status in {500, 503},
            details=details,
        )
    return AppError(
        ErrorCode.DEEPSEEK_API_ERROR,
        "The DeepSeek API rejected the request.",
        http_status=502,
        retryable=False,
        details=details,
    )


def _retry_after_seconds(response: httpx.Response, *, ceiling: float) -> float | None:
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        delay = float(raw)
    except ValueError:
        return None
    if delay < 0:
        return None
    return min(delay, ceiling)


class DeepSeekTransport:
    """HTTP client for the official DeepSeek chat completions API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._settings.deepseek_api_key.strip()
        if not key:
            raise _configuration_error()
        base = allowed_deepseek_base_url(self._settings.deepseek_base_url or DEEPSEEK_API_ROOT)
        timeout = httpx.Timeout(_timeout_seconds(self._settings))
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        async with DeepSeekAsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "DeepSeek request failed",
                request=response.request,
                response=response,
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise _invalid("The DeepSeek response was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise _invalid("The DeepSeek response was not valid JSON.")
        return data


class DeepSeekSession:
    """Shared chat session. Retries only safe transient failures."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Any | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        vision: bool = False,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleeper or _zero_sleep
        self._vision = vision

    def require_key(self) -> None:
        if not self._settings.deepseek_api_key.strip():
            raise _configuration_error()

    def _client(self) -> Any:
        self.require_key()
        if self._transport is None:
            self._transport = DeepSeekTransport(self._settings)
        return self._transport

    async def complete_text(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool = False,
        user_id: str | None = None,
    ) -> DeepSeekMessage:
        payload = self._payload(messages, json_mode=False, thinking=thinking, user_id=user_id)
        return await self._run(payload, parse_json=False)

    async def complete_json(
        self,
        messages: list[dict[str, Any]],
        *,
        user_id: str | None = None,
        required_keys: list[str] | None = None,
    ) -> tuple[DeepSeekMessage, dict[str, Any]]:
        payload = self._payload(messages, json_mode=True, thinking=False, user_id=user_id)
        message = await self._run(payload, parse_json=True, required_keys=required_keys)
        try:
            data = extract_json_object(message.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise _invalid("The DeepSeek response was not valid JSON.") from exc
        return message, data

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool,
        thinking: bool,
        user_id: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": _model_name(self._settings),
            "messages": messages,
            "temperature": self._settings.llm_temperature,
            "max_tokens": 2048 if json_mode else 4096,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if thinking:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "high"
        if user_id:
            from services.media_paths import assert_safe_user_id

            body["user"] = assert_safe_user_id(user_id)
        return body

    async def _run(
        self,
        payload: dict[str, Any],
        *,
        parse_json: bool,
        required_keys: list[str] | None = None,
    ) -> DeepSeekMessage:
        self.require_key()
        attempts = max(1, int(self._settings.llm_max_attempts))
        last_error: AppError | None = None
        for attempt in range(1, attempts + 1):
            try:
                message = self._parse_message(await self._chat(payload))
                if parse_json:
                    data = extract_json_object(message.text)
                    if required_keys and any(key not in data for key in required_keys):
                        raise _invalid("The DeepSeek response did not match the required JSON shape.")
                return message
            except (ValueError, json.JSONDecodeError, ValidationError) as exc:
                last_error = _invalid("The DeepSeek response was not valid JSON.")
                if attempt >= attempts:
                    raise last_error from exc
                await self._sleep(float(self._settings.openai_retry_delay_seconds))
            except AppError as exc:
                last_error = exc
                if exc.code == ErrorCode.DEEPSEEK_CONFIGURATION_ERROR or not exc.retryable or attempt >= attempts:
                    raise
                delay = exc.retry_after_seconds
                if delay is None:
                    delay = float(self._settings.openai_retry_delay_seconds)
                await self._sleep(min(float(delay), float(self._settings.max_retry_after_seconds)))
        raise last_error or AppError(
            ErrorCode.DEEPSEEK_UNAVAILABLE,
            "The DeepSeek API is unavailable.",
            http_status=503,
        )

    async def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            data = await self._client().chat(payload)
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.DEEPSEEK_TIMEOUT,
                "The DeepSeek API request timed out.",
                http_status=504,
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _status_error(
                exc.response.status_code,
                vision=self._vision,
                retry_after=_retry_after_seconds(exc.response, ceiling=self._settings.max_retry_after_seconds),
            ) from exc
        except httpx.TransportError as exc:
            code = ErrorCode.VISION_PROVIDER_ERROR if self._vision else ErrorCode.DEEPSEEK_UNAVAILABLE
            message = (
                "The DeepSeek vision provider is unavailable."
                if self._vision
                else "The DeepSeek API is unavailable."
            )
            raise AppError(code, message, http_status=503, retryable=True) from exc
        except Exception as exc:
            code = ErrorCode.VISION_PROVIDER_ERROR if self._vision else ErrorCode.DEEPSEEK_API_ERROR
            raise AppError(code, "The DeepSeek API request failed.", http_status=502, retryable=True) from exc
        if not isinstance(data, dict):
            raise _invalid("The DeepSeek response was not valid JSON.")
        return data

    def _parse_message(self, payload: dict[str, Any]) -> DeepSeekMessage:
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise _invalid("The DeepSeek response did not include any choices.")
        finish = choices[0].get("finish_reason")
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            raise _invalid("The DeepSeek response did not include a message.")
        if message.get("tool_calls"):
            raise _invalid("The DeepSeek response attempted a tool call, which is not allowed.", retryable=False)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise _invalid("The DeepSeek response did not include text content.")
        if finish == "length":
            raise _invalid("The DeepSeek response was truncated.")
        reasoning = message.get("reasoning_content")
        reasoning_text = reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None
        usage_raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        request_id = payload.get("id")
        model = payload.get("model") if isinstance(payload.get("model"), str) and payload.get("model") else _model_name(self._settings)
        return DeepSeekMessage(
            request_id=request_id if isinstance(request_id, str) else None,
            model=model,
            text=_strip_secret(self._settings, content),
            reasoning=_strip_secret(self._settings, reasoning_text) if reasoning_text else None,
            finish_reason=finish if isinstance(finish, str) else None,
            usage=TokenUsage.model_validate(usage_raw) if usage_raw else None,
        )


async def _zero_sleep(_: float) -> None:
    return None


def _context_payload(request: ContentPlanRequest, *, attempt: int, example: dict[str, Any]) -> str:
    payload = {
        "current_date": request.current_date,
        "user_prompt": request.user_prompt,
        "reason_hint": request.reason_hint,
        "business_profile": (
            request.business_profile.model_dump(exclude_none=True) if request.business_profile else None
        ),
        "festival": request.festival.model_dump(exclude_none=True) if request.festival else None,
        "recent_content": [item.model_dump(exclude_none=True) for item in request.recent_content],
        "themes_to_avoid": request.recent_themes(),
        "json_example": example,
        "retry_instruction": (
            "The previous attempt was invalid or repeated a recent theme. "
            "Choose a clearly different theme and return valid JSON only."
            if attempt > 1
            else None
        ),
    }
    return json.dumps(payload, default=str)


def _business_name(request: ContentPlanRequest) -> str | None:
    if request.business_profile and request.business_profile.business_name:
        name = request.business_profile.business_name.strip()
        return name or None
    return None


def _require_business_mention(text: str, business_name: str | None) -> None:
    if business_name and business_name.lower() not in text.lower():
        raise _invalid("The DeepSeek response was not specific to the business.")


def _theme_is_repeat(theme: str, recent_themes: list[str]) -> bool:
    current = theme.strip().lower()
    return any(current == item.strip().lower() for item in recent_themes)


class DeepSeekLLMProvider(LLMProvider):
    """Text reasoning, structured JSON, creative planning, captions, hashtags, and festival strategy."""

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
        self._session = DeepSeekSession(settings, transport=chosen, sleeper=sleeper, vision=False)

    async def reason(self, prompt: str, *, system: str | None = None, user_id: str | None = None) -> TextReasoning:
        text = (prompt or "").strip()
        if not text:
            raise AppError(ErrorCode.INVALID_REQUEST, "A prompt is required.", http_status=400)
        message = await self._session.complete_text(
            [
                {"role": "system", "content": system or TEXT_SYSTEM},
                {"role": "user", "content": text},
            ],
            thinking=True,
            user_id=user_id,
        )
        self._log("reason", message)
        return TextReasoning(
            model=message.model,
            text=message.text,
            reasoning=message.reasoning,
            request_id=message.request_id,
            finish_reason=message.finish_reason,
            usage=message.usage,
        )

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        required = None
        if isinstance(schema, dict) and isinstance(schema.get("required"), list):
            required = [str(item) for item in schema["required"]]
        system = JSON_SYSTEM
        if system_prompt and system_prompt.strip():
            system = system_prompt.strip() if "json" in system_prompt.lower() else f"{JSON_SYSTEM}\n{system_prompt.strip()}"
        message, data = await self._session.complete_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            user_id=user_id,
            required_keys=required,
        )
        self._log("generate_structured", message)
        return data

    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        recent = request.recent_themes()
        attempts = max(1, int(self._settings.llm_max_attempts))
        last_error: AppError | None = None
        example = {
            "content_type": "product_promotion",
            "theme": "festive_collection",
            "image_prompt": "A detailed still-image description at least twenty characters long.",
            "business_context": "How this image belongs to the business.",
            "reason": "user_prompt",
        }
        for attempt in range(1, attempts + 1):
            try:
                message, data = await self._session.complete_json(
                    [
                        {"role": "system", "content": JSON_SYSTEM},
                        {
                            "role": "user",
                            "content": _context_payload(request, attempt=attempt, example=example)
                            + "\n"
                            + json.dumps({"output_schema": content_plan_json_schema()}),
                        },
                    ],
                    required_keys=["content_type", "theme", "image_prompt", "business_context", "reason"],
                )
                plan = ContentPlan.model_validate(data)
            except AppError:
                raise
            except (ValidationError, ValueError) as exc:
                last_error = _invalid("The DeepSeek response was not a valid content plan.")
                if attempt >= attempts:
                    raise last_error from exc
                await self._session._sleep(float(self._settings.openai_retry_delay_seconds))
                continue
            if _theme_is_repeat(plan.theme, recent) and attempt < attempts:
                last_error = _invalid("The content plan repeated a recent theme.")
                await self._session._sleep(float(self._settings.openai_retry_delay_seconds))
                continue
            self._log("generate_content_plan", message)
            return plan
        raise last_error or _invalid("The DeepSeek response was not a valid content plan.")

    async def plan_creative(self, request: ContentPlanRequest) -> CreativePlan:
        return await self.generate_creative_brief(request)

    async def generate_creative_brief(self, request: ContentPlanRequest) -> CreativePlan:
        example = {
            "content_type": "festival",
            "theme": "diwali_silk_window",
            "concept": "A business-specific festive still.",
            "image_prompt": "A boutique window of silk sarees with warm diya light, composed for a square Instagram post.",
            "caption": "A caption grounded in this business.",
            "hashtags": ["Diwali", "Silk"],
            "business_context": "Why this creative belongs to the business.",
            "reason": "festival_campaign",
            "festival_name": "Diwali",
            "festival_strategy": "Feature this business's products during the festival.",
        }
        message, data = await self._json_task(request, example=example, task="plan_creative")
        try:
            plan = CreativePlan.model_validate(
                {**data, "provider": "deepseek", "model": message.model, "request_id": message.request_id}
            )
            ContentPlan.model_validate(plan.model_dump())
        except (ValidationError, ValueError) as exc:
            raise _invalid("The DeepSeek response was not a valid creative plan.") from exc
        _require_business_mention(
            " ".join([plan.concept, plan.caption, plan.business_context, plan.image_prompt]),
            _business_name(request),
        )
        self._log("plan_creative", message)
        return plan

    async def write_caption(self, request: CaptionRequest) -> CaptionResult:
        example = {"caption": "A short Instagram caption.", "language": "English"}
        message, data = await self._session.complete_json(
            [
                {"role": "system", "content": JSON_SYSTEM + "\nWrite one Instagram caption in JSON."},
                {"role": "user", "content": json.dumps({"task": "caption", "input": request.model_dump(), "json_example": example})},
            ],
            required_keys=["caption"],
        )
        try:
            result = CaptionResult.model_validate(
                {
                    "caption": data.get("caption"),
                    "language": data.get("language") or request.language,
                    "model": message.model,
                    "request_id": message.request_id,
                }
            )
        except (ValidationError, ValueError) as exc:
            raise _invalid("The DeepSeek response was not a valid caption.") from exc
        self._log("write_caption", message)
        return result

    async def suggest_hashtags(self, request: HashtagRequest) -> HashtagResult:
        example = {"hashtags": ["ShopLocal", "Festival"]}
        message, data = await self._session.complete_json(
            [
                {"role": "system", "content": JSON_SYSTEM + "\nReturn Instagram hashtags as a JSON array of strings."},
                {"role": "user", "content": json.dumps({"task": "hashtags", "input": request.model_dump(), "json_example": example})},
            ],
            required_keys=["hashtags"],
        )
        raw_tags = data.get("hashtags")
        if not isinstance(raw_tags, list):
            raise _invalid("The DeepSeek response was not a valid hashtag list.")
        try:
            result = HashtagResult.model_validate(
                {"hashtags": raw_tags[: max(1, request.limit)], "model": message.model, "request_id": message.request_id}
            )
        except (ValidationError, ValueError) as exc:
            raise _invalid("The DeepSeek response was not a valid hashtag list.") from exc
        self._log("suggest_hashtags", message)
        return result

    async def plan_festival(self, request: ContentPlanRequest) -> FestivalStrategy:
        festival_name = request.festival.festival_name if request.festival and request.festival.festival_name else ""
        example = {
            "festival_name": festival_name or "Diwali",
            "strategy": "Merchandise this business's own products for the festival.",
            "post_ideas": ["Window display", "Product detail"],
            "caption": "A caption that names the business.",
            "hashtags": ["Festival", "LocalBusiness"],
            "image_direction": "A square still of this business's products with festival lighting, not a generic greeting card.",
            "business_context": "How the festival post belongs to this business.",
        }
        message, data = await self._json_task(request, example=example, task="festival_strategy")
        try:
            strategy = FestivalStrategy.model_validate(
                {**data, "provider": "deepseek", "model": message.model, "request_id": message.request_id}
            )
        except (ValidationError, ValueError) as exc:
            raise _invalid("The DeepSeek response was not a valid festival strategy.") from exc
        blob = " ".join(
            [strategy.strategy, strategy.caption, strategy.image_direction, strategy.business_context, *strategy.post_ideas]
        )
        _require_business_mention(blob, _business_name(request))
        self._log("plan_festival", message)
        return strategy

    async def _json_task(
        self,
        request: ContentPlanRequest,
        *,
        example: dict[str, Any],
        task: str,
    ) -> tuple[DeepSeekMessage, dict[str, Any]]:
        return await self._session.complete_json(
            [
                {"role": "system", "content": JSON_SYSTEM},
                {"role": "user", "content": json.dumps({"task": task}) + "\n" + _context_payload(request, attempt=1, example=example)},
            ]
        )

    def _log(self, step: str, message: DeepSeekMessage) -> None:
        log_step(
            logger,
            event="DEEPSEEK_TEXT_COMPLETED",
            task_id="deepseek",
            step=step,
            tool="DeepSeekLLMProvider",
            status="success",
            model=message.model,
            provider="deepseek",
            request_id=message.request_id,
        )


class DeepSeekCreativeClient:
    """Adapter for creative-plan callers. It does not publish."""

    def __init__(self, settings: Settings, *, http: Any | None = None) -> None:
        self._settings = settings
        self._llm = DeepSeekLLMProvider(settings, client=http if http is not None and hasattr(http, "chat") else None)

    async def create_plan(self, context: Any, *, feedback: str | None = None) -> Any:
        from models.creative import CreativePlan as SourcedCreativePlan

        payload = context.for_model() if hasattr(context, "for_model") else {}
        if feedback:
            payload = {**payload, "feedback": redact_text(str(feedback))[:500]}
        example = {
            "campaign_type": "FESTIVAL",
            "festival": None,
            "business_type": None,
            "audience": None,
            "caption": "A caption grounded in the supplied business.",
            "hashtags": ["LocalBusiness"],
            "creative_direction": "Use only supplied products and offers.",
            "image_prompt": "A square still using only the supplied business facts and products.",
            "product_ids": [],
            "asset_ids": [],
            "canva_action": "none",
            "qa_requirements": ["Do not invent a price or offer."],
        }
        _message, data = await self._llm._session.complete_json(
            [
                {"role": "system", "content": JSON_SYSTEM},
                {"role": "user", "content": json.dumps({"task": "creative_plan", "context": payload, "json_example": example})},
            ]
        )
        try:
            return SourcedCreativePlan.model_validate(data)
        except (ValidationError, ValueError) as exc:
            raise _invalid("The DeepSeek response was not a valid creative plan.") from exc

    async def review_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        plan: Any,
        requirements: list[str],
    ) -> Any:
        from ai.vision.deepseek import DeepSeekVisionProvider, VisionImage
        from models.creative import ImageQAVerdict

        del mime_type
        vision = DeepSeekVisionProvider(self._settings, transport=self._llm._session._transport)
        image = VisionImage(user_id="creative-review", image_base64=_bytes_b64(image_bytes))
        brief = json.dumps(
            {
                "caption": getattr(plan, "caption", ""),
                "requirements": requirements,
            },
            default=str,
        )
        result = await vision.review_image(image, brief=brief)
        issues = list(result.violations) or ([] if result.passed else list(result.reasons))
        return ImageQAVerdict(passed=result.passed and not result.violations, issues=issues)


def _bytes_b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")
