"""Authenticated brand, product, and asset routes.

Filesystem paths are never returned. Missing and foreign ids both respond 404.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from auth.deps import get_db, require_password_ok
from db.models import User
from db.schemas import BrandColorWrite, BrandWrite, FontMetadataWrite, GuidelineWrite, ProductUpdate, ProductWrite
from models.errors import AppError, ErrorCode
from services.asset_catalog import AssetCatalog

router = APIRouter(tags=["assets"])

_ATTACHMENT_TYPES = frozenset({"image/svg+xml", "application/pdf", "text/plain", "text/markdown"})


def _catalog(request: Request, db: Session) -> AssetCatalog:
    return AssetCatalog(db, request.app.state.settings)


async def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await upload.read(64 * 1024)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise AppError(ErrorCode.FILE_TOO_LARGE, "The file exceeds the maximum allowed size.", http_status=413)
        chunks.append(block)
    return b"".join(chunks)


@router.post("/assets")
async def create_asset(
    request: Request,
    file: UploadFile = File(...),
    role: str = Form(...),
    scope: str | None = Form(None),
    product_id: str | None = Form(None),
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    catalog = _catalog(request, db)
    data = await _read_upload(file, catalog.limits.max_bytes)
    asset = catalog.upload(
        user.id,
        filename=file.filename,
        data=data,
        claimed_mime=file.content_type,
        role=role,
        scope=scope,
        product_id=product_id,
    )
    return {"asset": asset}


@router.get("/assets")
def list_assets(
    request: Request,
    scope: str | None = Query(default=None),
    role: str | None = Query(default=None),
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    assets = _catalog(request, db).list_assets(user.id, scope=scope, role=role)
    return {"assets": assets}


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"asset": _catalog(request, db).get_asset(user.id, asset_id)}


@router.get("/assets/{asset_id}/media")
def get_asset_media(
    asset_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> FileResponse:
    path, mime_type, filename = _catalog(request, db).open_media(user.id, asset_id)
    disposition = "attachment" if mime_type in _ATTACHMENT_TYPES else "inline"
    return FileResponse(
        path,
        media_type=mime_type,
        filename=filename,
        content_disposition_type=disposition,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/assets/{asset_id}")
def delete_asset(
    asset_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    _catalog(request, db).delete_asset(user.id, asset_id)
    return {"deleted": True}


@router.post("/products")
def create_product(
    body: ProductWrite,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"product": _catalog(request, db).create_product(user.id, body)}


@router.get("/products")
def list_products(
    request: Request,
    active: bool = Query(default=False),
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"products": _catalog(request, db).list_products(user.id, active_only=active)}


@router.get("/products/{product_id}")
def get_product(
    product_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"product": _catalog(request, db).get_product(user.id, product_id)}


@router.put("/products/{product_id}")
def update_product(
    product_id: str,
    body: ProductUpdate,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"product": _catalog(request, db).update_product(user.id, product_id, body)}


@router.post("/brand")
def save_brand(
    body: BrandWrite,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"brand": _catalog(request, db).upsert_brand(user.id, body)}


@router.get("/brand")
def get_brand(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"brand": _catalog(request, db).get_brand(user.id)}


class StudioGuidelines(BaseModel):
    model_config = ConfigDict(extra="ignore")

    voice: str | None = None
    tone: str | None = None
    primary_colors: list[str] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)
    do_not: str | None = None
    legal_name: str | None = None
    festival_preferences: list[str] = Field(default_factory=list)


def _studio_guidelines(brand: dict | None) -> dict:
    if not brand:
        return {
            "voice": None,
            "tone": None,
            "primary_colors": [],
            "fonts": [],
            "do_not": None,
            "legal_name": None,
            "festival_preferences": [],
        }
    texts = {str(item.get("title")): item.get("body") for item in brand.get("guidelines") or []}
    colors: list[str] = []
    for item in brand.get("brand_colors") or []:
        if isinstance(item, dict) and item.get("hex"):
            colors.append(str(item["hex"]))
        elif isinstance(item, str):
            colors.append(item)
    fonts: list[str] = []
    for item in brand.get("fonts") or []:
        if isinstance(item, dict) and item.get("family"):
            fonts.append(str(item["family"]))
        elif isinstance(item, str):
            fonts.append(item)
    prefs = texts.get("festival_preferences") or ""
    return {
        "voice": texts.get("voice"),
        "tone": texts.get("tone"),
        "primary_colors": colors,
        "fonts": fonts,
        "do_not": texts.get("do_not"),
        "legal_name": brand.get("company_name"),
        "festival_preferences": [part.strip() for part in str(prefs).split(",") if part.strip()],
    }


def _studio_asset(asset: dict) -> dict:
    role = str(asset.get("role") or "")
    return {
        "id": asset["id"],
        "kind": "LOGO" if role.startswith("logo") else role,
        "filename": asset.get("filename"),
        "mime_type": asset.get("mime_type"),
        "is_primary_logo": role.startswith("logo"),
        "status": "ACTIVE",
        "media_url": asset.get("media_url"),
    }


@router.get("/brand/guidelines")
def get_studio_guidelines(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    return {"guidelines": _studio_guidelines(_catalog(request, db).get_brand(user.id))}


@router.put("/brand/guidelines")
def save_studio_guidelines(
    body: StudioGuidelines,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    colors = []
    for item in body.primary_colors:
        text = item.strip()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
            colors.append(BrandColorWrite(hex=text.lower()))
    fonts = [FontMetadataWrite(family=item.strip()) for item in body.fonts if item.strip()]
    notes = []
    if body.voice:
        notes.append(GuidelineWrite(title="voice", body=body.voice))
    if body.tone:
        notes.append(GuidelineWrite(title="tone", body=body.tone))
    if body.do_not:
        notes.append(GuidelineWrite(title="do_not", body=body.do_not))
    if body.festival_preferences:
        notes.append(GuidelineWrite(title="festival_preferences", body=", ".join(body.festival_preferences)))
    saved = _catalog(request, db).upsert_brand(
        user.id,
        BrandWrite(
            company_name=(body.legal_name or "Brand").strip() or "Brand",
            brand_colors=colors,
            fonts=fonts,
            guidelines=notes or None,
        ),
    )
    return {"guidelines": _studio_guidelines(saved)}


@router.get("/brand/assets")
def list_brand_assets(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    assets = _catalog(request, db).list_assets(user.id, scope="brand")
    return {"assets": [_studio_asset(item) for item in assets]}


@router.post("/brand/assets")
async def upload_brand_asset(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("LOGO"),
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    catalog = _catalog(request, db)
    data = await _read_upload(file, catalog.limits.max_bytes)
    mime = (file.content_type or "").lower()
    role = "logo_svg" if kind.upper() == "LOGO" and "svg" in mime else "logo_png"
    if kind.upper() not in {"LOGO", "LOGO_PNG", "LOGO_SVG"}:
        role = "other"
    asset = catalog.upload(
        user.id,
        filename=file.filename,
        data=data,
        claimed_mime=file.content_type,
        role=role,
        scope="brand",
    )
    return {"asset": _studio_asset(asset)}


@router.delete("/brand/assets/{asset_id}")
def delete_brand_asset(
    asset_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    _catalog(request, db).delete_asset(user.id, asset_id)
    return {"deleted": True}


@router.get("/brand/assets/{asset_id}/media")
def brand_asset_media(
    asset_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> FileResponse:
    return get_asset_media(asset_id, request, user, db)


@router.post("/products/{product_id}/images")
async def upload_product_image(
    product_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    catalog = _catalog(request, db)
    data = await _read_upload(file, catalog.limits.max_bytes)
    catalog.upload(
        user.id,
        filename=file.filename,
        data=data,
        claimed_mime=file.content_type,
        role="product_image",
        scope="product",
        product_id=product_id,
    )
    return {"product": catalog.get_product(user.id, product_id)}


@router.get("/offers")
def list_offers(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    offers = []
    for product in _catalog(request, db).list_products(user.id):
        if not product.get("offer"):
            continue
        offers.append(
            {
                "id": product["id"],
                "product_id": product["id"],
                "title": product["name"],
                "body": product.get("offer"),
                "price_text": None if product.get("price") is None else str(product.get("price")),
                "status": "ACTIVE" if product.get("is_active") else "DRAFT",
            }
        )
    return {"offers": offers}


class OfferWrite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=255)
    body: str | None = None
    price_text: str | None = None
    product_id: str | None = None
    status: str | None = None


@router.post("/offers")
def create_offer(
    body: OfferWrite,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    catalog = _catalog(request, db)
    text = (body.body or body.title).strip()
    if body.product_id:
        product = catalog.update_product(user.id, body.product_id, ProductUpdate(offer=text))
    else:
        product = catalog.create_product(
            user.id,
            ProductWrite(name=body.title, offer=text, is_active=body.status != "DRAFT"),
        )
    return {
        "offer": {
            "id": product["id"],
            "product_id": product["id"],
            "title": product["name"],
            "body": product.get("offer"),
            "price_text": body.price_text,
            "status": "ACTIVE" if product.get("is_active") else "DRAFT",
        }
    }


@router.get("/ai/status")
def ai_status(request: Request, user: User = Depends(require_password_ok)) -> dict:
    del user
    status = request.app.state.settings.public_ai_status()
    forbidden = {"api_key", "token", "secret", "password"}
    return {key: value for key, value in status.items() if key not in forbidden}


@router.get("/mcp/status")
def mcp_status(request: Request, user: User = Depends(require_password_ok)) -> dict:
    del user
    from backend.mcp.permissions import MCP_TOOL_ALLOWLIST

    settings = request.app.state.settings
    return {
        "enabled": True,
        "mcp_enabled": True,
        "canva_enabled": bool(settings.canva_enabled),
        "canva_connected": bool(settings.canva_configured),
        "tools": [{"name": name, "available": True} for name in sorted(MCP_TOOL_ALLOWLIST)],
    }
