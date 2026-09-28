"""Turn an owned asset into the bytes a pixel provider may receive.

Text providers receive the public metadata only. A missing or ineligible file
is not replaced with a generated logo or product photo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.ai.image.base import ImageInput
from backend.platform.assets.catalog import ProviderTarget
from backend.platform.assets.errors import fail
from backend.platform.redaction import PATH_KEYS, public_payload


@dataclass(frozen=True, slots=True)
class PreparedAsset:
    eligible: bool
    public: dict[str, Any]
    image: ImageInput | None = field(repr=False)


def prepare_record(
    record: Any,
    target: ProviderTarget,
    data: bytes | None,
) -> PreparedAsset:
    public = record.public()
    view: dict[str, Any] = {
        "found": True,
        "eligible": False,
        "provider": target.value,
        "asset": public,
    }
    if target is ProviderTarget.TEXT:
        view["reason"] = "metadata_only"
        return PreparedAsset(False, _sealed(view), None)
    if not record.reference_eligible:
        view["reason"] = "not_a_reference_image"
        return PreparedAsset(False, _sealed(view), None)
    if not data:
        view["reason"] = "bytes_unavailable"
        return PreparedAsset(False, _sealed(view), None)
    view["eligible"] = True
    image = ImageInput(data=data, mime_type=record.mime_type, filename=record.filename)
    return PreparedAsset(True, _sealed(view), image)


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = public_payload(payload)
    if not isinstance(cleaned, dict):
        raise fail("INVALID_DEFINITION")
    if PATH_KEYS.intersection(cleaned):
        raise fail("INVALID_PATH")
    return cleaned
