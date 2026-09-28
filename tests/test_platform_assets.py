"""Platform asset capabilities: optional roles, tenant isolation, and catalog compatibility."""

from __future__ import annotations

import asyncio
import inspect
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from backend.platform.assets import (
    AssetAccessError,
    AssetRole,
    AssetStore,
    PlatformAssetService,
    ProviderTarget,
    bind_assets,
    instagram_marketing_assets,
)
from backend.platform.assets.validation import catalog_limits
from db.repositories import UserRepository
from db.schemas import UserCreate
from db.session import create_engine_from_url, init_db
from models.errors import AppError
from services.asset_catalog import AssetCatalog
from tests.helpers import test_settings

TENANT_A = "user-a"
TENANT_B = "user-b"
LOGO_NAME = "BlueLogo.png"


def _png(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (12, 40, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (20, 20, 20)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _store(tmp_path: Path | None = None, *, max_bytes: int = 8 * 1024 * 1024) -> AssetStore:
    root = None if tmp_path is None else tmp_path / "media"
    return AssetStore(limits=catalog_limits(max_bytes, 8192), media_root=root)


def _logo(store: AssetStore, tenant: str = TENANT_A) -> object:
    return store.put(
        tenant,
        filename=LOGO_NAME,
        data=_png(),
        role=AssetRole.COMPANY_LOGO,
        claimed_mime="image/png",
    )


def test_instagram_binding_is_optional_and_not_universal() -> None:
    instagram = instagram_marketing_assets()
    assert instagram.roles == frozenset(
        {
            AssetRole.COMPANY_LOGO,
            AssetRole.PRODUCT_IMAGE,
            AssetRole.BRAND_GUIDE,
            AssetRole.CAMPAIGN,
        }
    )
    assert AssetRole.DOCUMENT not in instagram.roles
    assert AssetRole.REFERENCE_IMAGE not in instagram.roles
    operations = bind_assets("operations_assistant", 1, [AssetRole.DOCUMENT])
    assert operations.roles == frozenset({AssetRole.DOCUMENT})
    assert bind_assets("echo", 1, []).roles == frozenset()


def test_missing_logo_is_not_invented() -> None:
    service = PlatformAssetService(_store(), instagram_marketing_assets())
    resolved = service.resolve_for_tenant(TENANT_A, AssetRole.COMPANY_LOGO)
    assert resolved == {"found": False, "role": "company_logo", "asset": None}
    prepared = service.prepare_for_tenant(TENANT_A, AssetRole.COMPANY_LOGO, ProviderTarget.IMAGE_EDIT)
    assert prepared.eligible is False
    assert prepared.image is None
    assert prepared.public["found"] is False


def test_ungranted_logo_is_not_opened() -> None:
    store = _store()
    logo = _logo(store)
    service = PlatformAssetService(store, bind_assets("operations_assistant", 1, [AssetRole.DOCUMENT]))
    with pytest.raises(AssetAccessError) as exc:
        service.resolve_for_tenant(TENANT_A, AssetRole.COMPANY_LOGO, logo.id)
    body = json.dumps(exc.value.as_dict())
    assert exc.value.code == "NOT_GRANTED"
    assert LOGO_NAME not in body
    assert "storage_key" not in body


def test_published_binding_version_does_not_change() -> None:
    first = instagram_marketing_assets(1)
    second = bind_assets("instagram_marketing", 2, [])
    assert AssetRole.COMPANY_LOGO in first.roles
    assert second.roles == frozenset()
    with pytest.raises(AttributeError):
        first.roles = frozenset()  # type: ignore[misc]


def test_tenant_cannot_read_revise_or_delete_another_tenants_asset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    logo = _logo(store)
    assert store.list_for_tenant(TENANT_B) == []
    assert store.get(TENANT_B, logo.id) is None
    with pytest.raises(AssetAccessError) as exc:
        store.require(TENANT_B, logo.id)
    assert exc.value.code == "NOT_FOUND"
    with pytest.raises(AssetAccessError) as missing:
        store.update_metadata(TENANT_B, logo.id, {"label": "stolen"})
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(AssetAccessError):
        store.delete(TENANT_B, logo.id)
    assert store.get(TENANT_A, logo.id) is not None
    key = logo._storage_key
    assert key is not None
    owned = store.resolve_owned_key(TENANT_A, key)
    assert owned.is_file()
    with pytest.raises(AssetAccessError) as crossed:
        store.resolve_owned_key(TENANT_B, key)
    assert crossed.value.code == "INVALID_PATH"
    audit = json.dumps([event.as_dict() for event in store.audit.events])
    assert LOGO_NAME not in audit
    assert "storage_key" not in audit
    assert key not in audit


def test_safe_filenames_mime_size_and_path_traversal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for name in ("../logo.png", "..\\logo.png", "C:/logo.png", "logo\x00.png", "a" * 181):
        with pytest.raises(AssetAccessError) as exc:
            store.put(TENANT_A, filename=name, data=_png(), role=AssetRole.COMPANY_LOGO, claimed_mime="image/png")
        assert exc.value.code == "INVALID_FILENAME"
    with pytest.raises(AssetAccessError) as empty:
        store.put(TENANT_A, filename="logo.png", data=b"", role=AssetRole.COMPANY_LOGO)
    assert empty.value.code == "INVALID_FILE"
    tiny = AssetStore(limits=catalog_limits(32, 8192), media_root=tmp_path / "small")
    with pytest.raises(AssetAccessError) as large:
        tiny.put(TENANT_A, filename="logo.png", data=_png(), role=AssetRole.COMPANY_LOGO, claimed_mime="image/png")
    assert large.value.code == "FILE_TOO_LARGE"
    with pytest.raises(AssetAccessError) as mime:
        store.put(
            TENANT_A,
            filename="guide.pdf",
            data=_png(),
            role=AssetRole.DOCUMENT,
            claimed_mime="application/pdf",
        )
    assert mime.value.code == "UNSUPPORTED_FORMAT"
    script = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><script>bad</script></svg>'
    with pytest.raises(AssetAccessError) as svg:
        store.put(TENANT_A, filename="logo.svg", data=script, role=AssetRole.COMPANY_LOGO, claimed_mime="image/svg+xml")
    assert svg.value.code == "INVALID_FILE"
    with pytest.raises(AssetAccessError) as traversal:
        store.resolve_owned_key(TENANT_A, "assets/user-a/brand/../../etc/passwd")
    assert traversal.value.code == "INVALID_PATH"
    with pytest.raises(AssetAccessError) as tenant:
        store.put("../etc", filename="logo.png", data=_png(), role=AssetRole.COMPANY_LOGO)
    assert tenant.value.code == "TENANT_REQUIRED"


def test_reference_eligibility_and_provider_preparation() -> None:
    store = _store()
    logo = _logo(store)
    svg = store.put(
        TENANT_A,
        filename="mark.svg",
        data=b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"></svg>',
        role=AssetRole.COMPANY_LOGO,
        claimed_mime="image/svg+xml",
    )
    campaign = store.put(
        TENANT_A,
        filename="poster.jpg",
        data=_jpeg(),
        role=AssetRole.CAMPAIGN,
        claimed_mime="image/jpeg",
    )
    private = store.put(
        TENANT_A,
        filename="note.png",
        data=_png(),
        role=AssetRole.AGENT_ASSET,
        claimed_mime="image/png",
    )
    assert logo.reference_eligible is True
    assert svg.reference_eligible is False
    assert campaign.reference_eligible is True
    assert private.reference_eligible is False
    service = PlatformAssetService(store, instagram_marketing_assets())
    text = service.prepare_for_tenant(TENANT_A, AssetRole.COMPANY_LOGO, ProviderTarget.TEXT, logo.id)
    assert text.image is None
    assert text.public["reason"] == "metadata_only"
    assert "data" not in text.public
    pixels = service.prepare_for_tenant(TENANT_A, AssetRole.COMPANY_LOGO, ProviderTarget.IMAGE_EDIT, logo.id)
    assert pixels.eligible is True
    assert pixels.image is not None
    assert pixels.image.data.startswith(b"\x89PNG")
    dumped = json.dumps(pixels.public)
    assert "storage_key" not in dumped
    assert LOGO_NAME in dumped
    vector = service.prepare_for_tenant(TENANT_A, AssetRole.COMPANY_LOGO, ProviderTarget.IMAGE_EDIT, svg.id)
    assert vector.eligible is False
    assert vector.image is None
    assert vector.public["reason"] == "not_a_reference_image"


def test_metadata_revision_keeps_bytes_and_drops_paths() -> None:
    store = _store()
    logo = _logo(store)
    original = store.read_bytes(TENANT_A, logo.id)
    revised = store.update_metadata(
        TENANT_A,
        logo.id,
        {"label": "spring", "storage_key": "assets/evil", "access_token": "secret-token"},
    )
    assert revised.revision == 2
    assert revised.metadata == {"label": "spring"}
    assert store.read_bytes(TENANT_A, logo.id) == original
    assert "storage_key" not in revised.public()


def test_deletion_removes_the_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    logo = _logo(store)
    path = store.resolve_owned_key(TENANT_A, logo._storage_key or "")
    store.delete(TENANT_A, logo.id)
    assert not path.exists()
    assert store.get(TENANT_A, logo.id) is None
    with pytest.raises(AssetAccessError) as exc:
        store.require(TENANT_A, logo.id)
    assert exc.value.code == "NOT_FOUND"


def test_execution_uses_context_tenant_not_payload_tenant() -> None:
    store = _store()
    own = _logo(store, TENANT_A)
    other = _logo(store, TENANT_B)
    service = PlatformAssetService(store, instagram_marketing_assets())
    ctx = SimpleNamespace(
        tenant_id=TENANT_A,
        snapshot=SimpleNamespace(asset_roles=frozenset({"company_logo"})),
        payload={"tenant_id": TENANT_B, "asset_id": other.id},
    )
    hidden = asyncio.run(service.resolve(ctx, "company_logo", other.id))
    assert hidden["found"] is False
    visible = asyncio.run(service.resolve(ctx, "company_logo", own.id))
    assert visible["found"] is True
    assert visible["asset"]["id"] == own.id
    with pytest.raises(AssetAccessError) as exc:
        service.resolve_for_tenant(TENANT_A, "company_logo", granted=frozenset())
    assert exc.value.code == "NOT_GRANTED"


def test_catalog_bridge_preserves_owner_scoped_asset_api(tmp_path: Path) -> None:
    signature = inspect.signature(AssetCatalog.upload)
    assert {"user_id", "filename", "data", "claimed_mime", "role"} <= set(signature.parameters)
    settings = test_settings(tmp_path)
    engine = create_engine_from_url(settings.database_url)
    init_db(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        users = UserRepository(session)
        user_a = users.create(UserCreate(email="a@example.com", password_hash="hash"))
        user_b = users.create(UserCreate(email="b@example.com", password_hash="hash"))
        catalog = AssetCatalog(session, settings)
        store = AssetStore(limits=catalog.limits, media_root=Path(settings.media_root))
        record = store.upload_through_catalog(
            catalog,
            user_a.id,
            filename=LOGO_NAME,
            data=_png(),
            claimed_mime="image/png",
            role="logo_png",
        )
        owned = catalog.get_asset(user_a.id, record.id)
        assert owned["role"] == "logo_png"
        assert owned["media_url"] == f"/api/v1/assets/{record.id}/media"
        assert "storage_key" not in owned
        assert record.role is AssetRole.COMPANY_LOGO
        assert "storage_key" not in record.public()
        with pytest.raises(AppError) as denied:
            catalog.get_asset(user_b.id, record.id)
        assert denied.value.http_status == 404
        assert store.get(user_b.id, record.id) is None
        service = PlatformAssetService(store, instagram_marketing_assets(), catalog=catalog)
        prepared = service.prepare_for_tenant(
            user_a.id,
            AssetRole.COMPANY_LOGO,
            ProviderTarget.IMAGE_EDIT,
            record.id,
        )
        assert prepared.image is not None
        assert str(settings.media_root) not in json.dumps(prepared.public)
        foreign = service.prepare_for_tenant(
            user_b.id,
            AssetRole.COMPANY_LOGO,
            ProviderTarget.IMAGE_EDIT,
            record.id,
        )
        assert foreign.image is None
        assert foreign.public["found"] is False
        store.delete(user_a.id, record.id, catalog=catalog)
        with pytest.raises(AppError) as gone:
            catalog.get_asset(user_a.id, record.id)
        assert gone.value.http_status == 404
    finally:
        session.close()


def test_platform_asset_package_does_not_name_the_publisher() -> None:
    root = Path(__file__).resolve().parents[1] / "backend" / "platform" / "assets"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "InstagramAgent" not in text
    assert "enqueue_instagram" not in text
