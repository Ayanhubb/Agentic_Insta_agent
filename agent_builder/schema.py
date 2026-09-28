"""Request models for agent configuration.

Extra fields are rejected. Provider secrets and executable code are rejected
before a value is stored.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|credential|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+|access_token=\S+)",
    re.IGNORECASE,
)
_CODE_KEYS = frozenset(
    {
        "callable",
        "code",
        "command",
        "commands",
        "eval",
        "exec",
        "handler",
        "handler_path",
        "import",
        "import_path",
        "python",
        "script",
        "shell",
        "subprocess",
    }
)
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DOTTED = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")

ModelRoleName = Literal["content_planning", "image_generation", "vision_qa"]
ImplementationName = Literal["deepseek", "openai", "mock"]


def reject_untrusted(value: object, *, depth: int = 0) -> None:
    """Raise ValueError when a payload carries code or a provider secret."""

    if depth > 8:
        raise ValueError("Agent configuration cannot include code or provider secrets.")
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if name in _CODE_KEYS or _SECRET_KEY.search(name):
                raise ValueError("Agent configuration cannot include code or provider secrets.")
            reject_untrusted(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            reject_untrusted(item, depth=depth + 1)
        return
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError("Agent configuration cannot include code or provider secrets.")


class ModelBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ModelRoleName
    implementation: ImplementationName
    model: str = ""

    @field_validator("model")
    @classmethod
    def model_name(cls, value: str) -> str:
        reject_untrusted(value)
        text = value.strip()
        if len(text) > 128:
            raise ValueError("model name is too long")
        return text

    @model_validator(mode="after")
    def role_implementation(self) -> ModelBindingInput:
        allowed = {
            "content_planning": {"deepseek", "openai", "mock"},
            "image_generation": {"openai", "mock"},
            "vision_qa": {"deepseek", "mock"},
        }
        if self.implementation not in allowed[self.role]:
            raise ValueError("That provider is not available for this role.")
        return self


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)

    @field_validator("key")
    @classmethod
    def agent_key(cls, value: str) -> str:
        if _KEY.fullmatch(value) is None:
            raise ValueError("agent key is not valid")
        return value

    @model_validator(mode="after")
    def no_secrets(self) -> AgentCreate:
        reject_untrusted(self.model_dump())
        return self


class AgentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    models: list[ModelBindingInput] | None = None
    tool_names: list[str] | None = None
    knowledge_sources: list[str] | None = None
    asset_roles: list[str] | None = None
    workflow_key: str | None = None
    trigger_key: str | None = None
    approval_policy_id: str | None = None
    timeout_seconds: float | None = None

    @field_validator("workflow_key", "trigger_key", "approval_policy_id")
    @classmethod
    def dotted(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _DOTTED.fullmatch(value) is None:
            raise ValueError("identifier is not valid")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def timeout(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0 or value > 3600:
            raise ValueError("timeout must be greater than 0 and at most 3600 seconds")
        return float(value)

    @model_validator(mode="after")
    def no_secrets(self) -> AgentPatch:
        reject_untrusted(self.model_dump())
        if self.models:
            roles = [item.role for item in self.models]
            if len(roles) != len(set(roles)):
                raise ValueError("Each model role is configured once.")
        return self


class CloneAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("key")
    @classmethod
    def agent_key(cls, value: str) -> str:
        if _KEY.fullmatch(value) is None:
            raise ValueError("agent key is not valid")
        return value

    @model_validator(mode="after")
    def no_secrets(self) -> CloneAgent:
        reject_untrusted(self.model_dump())
        return self


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_number: int = Field(ge=1)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_key: str | None = None
    version_number: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("trigger_key")
    @classmethod
    def trigger(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _DOTTED.fullmatch(value) is None:
            raise ValueError("trigger key is not valid")
        return value

    @model_validator(mode="after")
    def no_secrets(self) -> ExecuteRequest:
        reject_untrusted({"trigger_key": self.trigger_key, "payload": self.payload})
        return self
