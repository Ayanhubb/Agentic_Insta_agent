"""Persistence-layer errors. Route handlers should map these to HTTP; they are not AppError."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


class PersistenceError(Exception):
    """Base class for database/domain errors."""


class DuplicateRecordError(PersistenceError):
    """Raised when a unique constraint would be violated (duplicate daily post, email, job, …)."""

    def __init__(self, message: str, *, entity: str | None = None, constraint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.entity = entity
        self.constraint = constraint

    @classmethod
    def from_integrity(cls, exc: IntegrityError) -> "DuplicateRecordError":
        orig = str(getattr(exc, "orig", exc))
        constraint = None
        entity = None
        lowered = orig.lower()
        if "uq_ig_posts_daily_published" in orig or "daily_retail" in lowered:
            entity = "instagram_posts"
            constraint = "uq_ig_posts_daily_published"
            message = "A verified DAILY_RETAIL_POST already exists for this account and scheduled date."
        elif "users" in lowered and "email" in lowered:
            entity = "users"
            constraint = "uq_users_email"
            message = "A user with this email already exists."
        elif "scheduled_jobs" in lowered:
            entity = "scheduled_jobs"
            constraint = "uq_scheduled_jobs_user_account_type"
            message = "An autonomous job of this type already exists for this Instagram account."
        elif "uq_products_user_sku" in orig or ("products" in lowered and "sku" in lowered):
            entity = "products"
            constraint = "uq_products_user_sku"
            message = "A product with this SKU already exists."
        elif "trend_observations" in lowered and "external_id" in lowered:
            entity = "trend_observations"
            constraint = "uq_trend_observations_source_external"
            message = "This trend observation was already stored for the source."
        elif "festival_campaigns" in lowered:
            entity = "festival_campaigns"
            constraint = "uq_festival_campaigns_user_name_year"
            message = "A festival campaign with this name and year already exists for the user."
        else:
            message = "A unique constraint was violated."
        return cls(message, entity=entity, constraint=constraint)


class RecordNotFoundError(PersistenceError):
    def __init__(self, entity: str, record_id: str) -> None:
        super().__init__(f"{entity} {record_id} was not found.")
        self.entity = entity
        self.record_id = record_id


class TokenEncryptionError(PersistenceError):
    """Raised when a token cannot be stored or read without plaintext fallback."""
