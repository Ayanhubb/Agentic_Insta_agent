"""Normalize Canva MCP tool payloads into application contracts.

Unknown fields are dropped. Secret-shaped keys are removed before mapping.
"""

from __future__ import annotations

from typing import Any

from backend.integrations.canva.contracts import (
    CanvaBrandAsset,
    CanvaDesignCandidate,
    CanvaDesignCreation,
    CanvaDesignSearch,
    CanvaDesignSummary,
    CanvaExportResult,
)
from services.logging import is_secret_key


def strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): strip_secrets(item)
            for key, item in value.items()
            if not is_secret_key(str(key))
        }
    if isinstance(value, list):
        return [strip_secrets(item) for item in value]
    return value


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _thumbnail_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                urls.append(item.strip())
            elif isinstance(item, dict):
                url = _text(item.get("url"))
                if url:
                    urls.append(url)
    elif isinstance(value, dict):
        url = _text(value.get("url"))
        if url:
            urls.append(url)
    elif isinstance(value, str) and value.strip():
        urls.append(value.strip())
    return urls


def design_summary(payload: dict[str, Any]) -> CanvaDesignSummary | None:
    source = payload.get("design_summary") or payload.get("design") or payload
    if not isinstance(source, dict):
        return None
    design_id = _text(source.get("id")) or _text(source.get("design_id"))
    if not design_id:
        return None
    urls = source.get("urls") if isinstance(source.get("urls"), dict) else {}
    thumbnails = _thumbnail_urls(source.get("thumbnails") or source.get("thumbnail"))
    page_count = source.get("page_count")
    return CanvaDesignSummary(
        id=design_id,
        title=_text(source.get("title")) or _text(source.get("text")),
        edit_url=_text(urls.get("edit_url")) or _text(source.get("edit_url")),
        view_url=_text(urls.get("view_url")) or _text(source.get("view_url")) or _text(source.get("url")),
        page_count=page_count if isinstance(page_count, int) else None,
        thumbnail_url=thumbnails[0] if thumbnails else None,
    )


def creation_from_generation(payload: dict[str, Any]) -> CanvaDesignCreation:
    job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    job_id = _text(job.get("id")) if isinstance(job, dict) else None
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else {}
    raw_designs = result.get("generated_designs") if isinstance(result, dict) else None
    if not isinstance(raw_designs, list) and isinstance(job, dict):
        raw_designs = job.get("generated_designs")
    candidates: list[CanvaDesignCandidate] = []
    for item in raw_designs or []:
        if not isinstance(item, dict):
            continue
        candidate_id = _text(item.get("candidate_id"))
        if not candidate_id:
            continue
        candidates.append(
            CanvaDesignCandidate(
                candidate_id=candidate_id,
                url=_text(item.get("url")),
                thumbnail_urls=_thumbnail_urls(item.get("thumbnails")),
            )
        )
    status = _text(job.get("status")) if isinstance(job, dict) else None
    return CanvaDesignCreation(
        status="candidates_ready" if candidates else (status or "pending"),
        job_id=job_id,
        candidates=candidates,
        design=None,
    )


def creation_from_design(payload: dict[str, Any], *, job_id: str | None) -> CanvaDesignCreation:
    return CanvaDesignCreation(
        status="created",
        job_id=job_id,
        candidates=[],
        design=design_summary(payload),
    )


def search_results(payload: dict[str, Any]) -> CanvaDesignSearch:
    raw = payload.get("results") or payload.get("items") or payload.get("designs") or []
    designs: list[CanvaDesignSummary] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                summary = design_summary(item)
                if summary is not None:
                    designs.append(summary)
    continuation = payload.get("continuation")
    return CanvaDesignSearch(
        designs=designs,
        continuation=continuation if isinstance(continuation, str) and continuation else None,
    )


def export_result(payload: dict[str, Any], *, format_name: str) -> CanvaExportResult:
    job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    urls = job.get("urls") if isinstance(job, dict) else None
    download_urls = [url for url in urls if isinstance(url, str) and url.strip()] if isinstance(urls, list) else []
    status = _text(job.get("status")) if isinstance(job, dict) else None
    job_id = _text(job.get("id")) if isinstance(job, dict) else None
    return CanvaExportResult(
        job_id=job_id,
        status=status or ("success" if download_urls else "pending"),
        format=format_name,
        download_urls=download_urls,
    )


def supported_formats(payload: dict[str, Any]) -> list[str] | None:
    raw = payload.get("formats") or payload.get("export_formats") or payload.get("results")
    if not isinstance(raw, list):
        return None
    found: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
        elif isinstance(item, dict):
            value = item.get("type") or item.get("format") or item.get("name")
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
    return found or None


def brand_assets(payload: dict[str, Any], *, kind: str) -> list[CanvaBrandAsset]:
    raw = (
        payload.get("brand_kits")
        or payload.get("brand_templates")
        or payload.get("assets")
        or payload.get("items")
        or payload.get("results")
        or []
    )
    assets: list[CanvaBrandAsset] = []
    if not isinstance(raw, list):
        return assets
    for item in raw:
        if not isinstance(item, dict):
            continue
        asset_id = (
            _text(item.get("id"))
            or _text(item.get("brand_kit_id"))
            or _text(item.get("asset_id"))
            or _text(item.get("template_id"))
        )
        if not asset_id:
            continue
        thumbnails = _thumbnail_urls(item.get("thumbnails") or item.get("thumbnail") or item.get("icon"))
        assets.append(
            CanvaBrandAsset(
                id=asset_id,
                name=_text(item.get("name")) or _text(item.get("title")),
                kind=kind,
                thumbnail_url=thumbnails[0] if thumbnails else None,
            )
        )
    return assets


def transaction_id(payload: dict[str, Any]) -> str:
    transaction = payload.get("transaction") if isinstance(payload.get("transaction"), dict) else {}
    return (
        _text(transaction.get("transaction_id"))
        or _text(transaction.get("id"))
        or _text(payload.get("transaction_id"))
        or ""
    )


def continuation_of(payload: dict[str, Any]) -> str | None:
    value = payload.get("continuation")
    return value if isinstance(value, str) and value else None
