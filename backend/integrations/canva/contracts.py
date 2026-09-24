"""Application contracts for Canva results.

These models are the only Canva payloads returned to the rest of the app.
OAuth tokens, client secrets, and PKCE verifiers are not fields on them.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from models.errors import AppError, ErrorCode


@dataclass(frozen=True)
class CanvaOwner:
    """Tenant and user that own a Canva connection.

    This application has no separate organization table yet, so API routes set
    both ids from the authenticated user. Callers must not take either id from
    model output or from a request body.
    """

    tenant_id: str
    user_id: str

    def __post_init__(self) -> None:
        tenant_id = self.tenant_id.strip()
        user_id = self.user_id.strip()
        if not tenant_id or not user_id:
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "Canva operations require the signed-in user.",
                http_status=403,
            )
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "user_id", user_id)


def owner_from_user_id(user_id: str) -> CanvaOwner:
    return CanvaOwner(tenant_id=user_id, user_id=user_id)


class CanvaDesignSummary(BaseModel):
    id: str
    title: str | None = None
    edit_url: str | None = None
    view_url: str | None = None
    page_count: int | None = None
    thumbnail_url: str | None = None


class CanvaDesignCandidate(BaseModel):
    candidate_id: str
    url: str | None = None
    thumbnail_urls: list[str] = Field(default_factory=list)


class CanvaDesignCreation(BaseModel):
    status: str
    job_id: str | None = None
    candidates: list[CanvaDesignCandidate] = Field(default_factory=list)
    design: CanvaDesignSummary | None = None


class CanvaDesignSearch(BaseModel):
    designs: list[CanvaDesignSummary] = Field(default_factory=list)
    continuation: str | None = None


class CanvaExportResult(BaseModel):
    job_id: str | None = None
    status: str
    format: str
    download_urls: list[str] = Field(default_factory=list)


class CanvaBrandAsset(BaseModel):
    id: str
    name: str | None = None
    kind: str
    thumbnail_url: str | None = None


class CanvaBrandAssets(BaseModel):
    assets: list[CanvaBrandAsset] = Field(default_factory=list)
    continuation: str | None = None


class CanvaEditResult(BaseModel):
    design_id: str
    transaction_id: str
    status: str


class CanvaAccountStatus(BaseModel):
    enabled: bool
    configured: bool
    connected: bool
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)

    def public_dict(self) -> dict[str, object]:
        return self.model_dump()
