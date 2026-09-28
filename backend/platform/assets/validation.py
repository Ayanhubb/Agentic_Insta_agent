"""Intake checks. Type, size, and paths reuse the existing asset helpers.

The caller never supplies a storage path. A name is a label, not a location.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.platform.assets.catalog import (
    ROLE_SCOPE,
    AssetRole,
    is_reference_eligible,
)
from backend.platform.assets.errors import fail
from models.errors import AppError, ErrorCode
from services.asset_paths import assert_upload_filename
from services.asset_validation import validate_asset_bytes
from tools.image_validator import ImageLimits

# ``validate_asset_bytes`` knows the roles stored on ``business_assets``.
_VALIDATION_ROLE = {
    AssetRole.COMPANY_LOGO: "logo_png",
    AssetRole.PRODUCT_IMAGE: "product_image",
    AssetRole.BRAND_GUIDE: "guideline",
    AssetRole.CAMPAIGN: "campaign",
    AssetRole.DOCUMENT: "guideline",
    AssetRole.REFERENCE_IMAGE: "product_image",
    AssetRole.AGENT_ASSET: "other",
}


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    mime_type: str
    width: int | None
    height: int | None
    stored_role: str
    scope: str
    reference_eligible: bool


def catalog_limits(max_bytes: int, max_side: int) -> ImageLimits:
    """Same floor the brand catalog uses. Publish-image minimums do not apply."""
    return ImageLimits(
        max_bytes=max_bytes,
        min_width=16,
        min_height=16,
        max_width=max_side,
        max_height=max_side,
    )


def validate_upload(
    data: bytes,
    *,
    role: AssetRole,
    filename: str | None,
    claimed_mime: str | None,
    limits: ImageLimits,
) -> ValidatedUpload:
    label = _filename(filename)
    stored_role = _stored_role(role, data)
    try:
        mime, width, height = validate_asset_bytes(
            data,
            role=stored_role,
            claimed_mime=claimed_mime,
            limits=limits,
        )
    except AppError as exc:
        raise _from_app_error(exc) from exc
    return ValidatedUpload(
        filename=label,
        mime_type=mime,
        width=width,
        height=height,
        stored_role=stored_role,
        scope=ROLE_SCOPE[role],
        reference_eligible=is_reference_eligible(role, mime, stored_role),
    )


def _stored_role(role: AssetRole, data: bytes) -> str:
    if role is not AssetRole.COMPANY_LOGO:
        return _VALIDATION_ROLE[role]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "logo_png"
    sample = data[:512].lstrip().lower()
    if sample.startswith(b"<svg") or (sample.startswith(b"<?xml") and b"<svg" in sample):
        return "logo_svg"
    return "logo_png"


def _filename(filename: str | None) -> str:
    try:
        return assert_upload_filename(filename)
    except AppError as exc:
        raise _from_app_error(exc) from exc


def as_asset_error(exc: AppError) -> Exception:
    """Map a catalog or path error onto a platform code. The message stays generic."""
    return _from_app_error(exc)


def _from_app_error(exc: AppError) -> Exception:
    code = exc.code.value if isinstance(exc.code, ErrorCode) else str(exc.code)
    text = str(exc).lower()
    if code == ErrorCode.FILE_TOO_LARGE.value:
        return fail("FILE_TOO_LARGE")
    if code == ErrorCode.UNSUPPORTED_FORMAT.value:
        return fail("UNSUPPORTED_FORMAT")
    if code in {ErrorCode.INVALID_FILE.value, ErrorCode.INVALID_DIMENSIONS.value}:
        return fail("INVALID_FILE")
    if code == ErrorCode.NOT_FOUND.value:
        return fail("NOT_FOUND")
    if "file name" in text:
        return fail("INVALID_FILENAME")
    if "storage" in text or "path" in text or "user id" in text:
        return fail("INVALID_PATH")
    return fail("INVALID_DEFINITION")
