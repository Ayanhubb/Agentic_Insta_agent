"""OpenAI chat-completions LLM provider.

The provider:
- reads the model name from configuration
- returns schema-validated ContentPlan objects
- never calls Instagram, the database, or the shell
- never logs or returns the API key
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from pydantic import ValidationError

from config import Settings
from models.errors import AppError, ErrorCode
from services.logging import log_step, redact_text

from ai.llm_client import LLMProvider
from ai.schemas import ContentPlan, ContentPlanRequest, content_plan_json_schema

logger = logging.getLogger(__name__)

JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

SYSTEM_INSTRUCTIONS = """You are the content-planning model for an Instagram business assistant.

You only produce a JSON object that matches the required schema. You do not write code,
shell commands, HTTP requests, database queries, or Instagram API calls. You do not
ask for API keys, OAuth tokens, or credentials.

Responsibilities:
1. Understand the user prompt when one is provided.
2. Improve it into a detailed, Instagram-ready image-generation prompt.
3. Ground the plan in the business profile (name, type, products, services, brand style, audience, location, language).
4. Use festival context when present so the image is business-specific, not a generic greeting card.
5. Read recent content history and choose a different theme, product angle, and composition.
6. Return a structured content plan.
7. Avoid repeating recent themes.
8. Return only schema-valid JSON.

Required JSON keys:
- content_type
- theme
- image_prompt
- business_context
- reason

image_prompt must be a concrete visual description (subject, setting, lighting, composition, brand cues, products).
Do not include hashtags, captions, or publish instructions.
"""


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sleep = sleeper or asyncio.sleep

    def _require_configuration(self) -> None:
        if not self._settings.openai_api_key.strip():
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "OpenAI is not configured.",
                http_status=503,
            )
        if not self._settings.llm_model.strip():
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "An LLM model is not configured.",
                http_status=503,
            )

    def _get_client(self) -> Any:
        if self._client is None:
            self._require_configuration()
            self._client = AsyncOpenAI(
                api_key=self._settings.openai_api_key,
                timeout=self._settings.request_timeout_seconds,
            )
        else:
            self._require_configuration()
        return self._client

    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        self._require_configuration()
        recent_themes = request.recent_themes()
        last_error: AppError | None = None
        attempts = max(1, int(self._settings.llm_max_attempts))

        for attempt in range(1, attempts + 1):
            try:
                raw = await self._complete(request, recent_themes=recent_themes, attempt=attempt)
                plan = parse_content_plan(raw)
                if _theme_is_repeat(plan.theme, recent_themes) and attempt < attempts:
                    last_error = AppError(
                        ErrorCode.OPENAI_INVALID_RESPONSE,
                        "The content plan repeated a recent theme.",
                        http_status=502,
                    )
                    await self._sleep(self._settings.openai_retry_delay_seconds)
                    continue
                log_step(
                    logger,
                    event="LLM_PLAN_CREATED",
                    task_id="llm",
                    step="generate_content_plan",
                    tool="OpenAILLMProvider",
                    status="success",
                    content_type=plan.content_type,
                    theme=plan.theme,
                    reason=plan.reason,
                    attempt=attempt,
                    model=self._settings.llm_model,
                    provider=self._settings.llm_provider,
                )
                return plan
            except AppError as exc:
                last_error = exc
                if exc.code == ErrorCode.OPENAI_CONFIGURATION_ERROR:
                    raise
                if not exc.retryable or attempt >= attempts:
                    raise
                await self._sleep(self._settings.openai_retry_delay_seconds)

        raise last_error or AppError(
            ErrorCode.OPENAI_API_ERROR,
            "The OpenAI request failed.",
            http_status=502,
        )

    async def _complete(
        self,
        request: ContentPlanRequest,
        *,
        recent_themes: list[str],
        attempt: int,
    ) -> str:
        client = self._get_client()
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": build_user_message(request, recent_themes, attempt=attempt)},
        ]
        try:
            response = await client.chat.completions.create(
                model=self._settings.llm_model,
                messages=messages,
                temperature=self._settings.llm_temperature,
                response_format={"type": "json_object"},
            )
        except AuthenticationError as exc:
            raise _configuration_error() from exc
        except RateLimitError as exc:
            raise _api_error("The OpenAI API rate limit was reached.", retryable=True, status=429) from exc
        except (APITimeoutError, APIConnectionError) as exc:
            raise _api_error("The OpenAI API request timed out.", retryable=True, status=504) from exc
        except BadRequestError as exc:
            raise _api_error("The OpenAI API rejected the request.", retryable=False) from exc
        except APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code == 429
            raise _api_error(
                "The OpenAI API request failed.",
                retryable=retryable,
                status=exc.status_code,
            ) from exc
        except Exception as exc:
            raise _api_error("The OpenAI API request failed.", retryable=True) from exc

        return _message_text(response)


def build_user_message(
    request: ContentPlanRequest,
    recent_themes: list[str],
    *,
    attempt: int,
) -> str:
    payload = {
        "current_date": request.current_date,
        "user_prompt": request.user_prompt,
        "reason_hint": request.reason_hint,
        "business_profile": (
            request.business_profile.model_dump(exclude_none=True) if request.business_profile else None
        ),
        "festival": request.festival.model_dump(exclude_none=True) if request.festival else None,
        "recent_content": [item.model_dump(exclude_none=True) for item in request.recent_content],
        "themes_to_avoid": recent_themes,
        "output_schema": content_plan_json_schema(),
        "retry_instruction": (
            "The previous attempt was invalid or repeated a recent theme. "
            "Choose a clearly different theme and return valid JSON only."
            if attempt > 1
            else None
        ),
    }
    return json.dumps(payload, default=str)


def parse_content_plan(raw: str) -> ContentPlan:
    try:
        data = extract_json_object(raw)
        return ContentPlan.model_validate(data)
    except (ValueError, ValidationError, TypeError) as exc:
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI response was not a valid content plan.",
            http_status=502,
            retryable=True,
        ) from exc


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


def _message_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI response did not include any choices.",
            http_status=502,
            retryable=True,
        )
    message = getattr(choices[0], "message", None)
    if message is None:
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI response did not include a message.",
            http_status=502,
            retryable=True,
        )
    if getattr(message, "tool_calls", None):
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI response attempted a tool call, which is not allowed.",
            http_status=502,
        )
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI response did not include text content.",
            http_status=502,
            retryable=True,
        )
    return content


def _theme_is_repeat(theme: str, recent_themes: list[str]) -> bool:
    current = theme.strip().lower()
    return any(current == item.strip().lower() for item in recent_themes)


def _configuration_error() -> AppError:
    return AppError(
        ErrorCode.OPENAI_CONFIGURATION_ERROR,
        "OpenAI is not configured.",
        http_status=503,
    )


def _api_error(message: str, *, retryable: bool, status: int | None = None) -> AppError:
    safe = redact_text(message)
    details: dict[str, Any] = {}
    if status is not None:
        details["status_code"] = status
    return AppError(
        ErrorCode.OPENAI_API_ERROR,
        safe,
        http_status=504 if status == 504 else 502,
        retryable=retryable,
        details=details,
    )
