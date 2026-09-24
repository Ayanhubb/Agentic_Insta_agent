"""Tenant asset, brand, and product isolation."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent.planner import ALLOWED_TOOL_SET
from api.app import create_app
from db.models import BusinessAsset
from models.errors import AppError
from services.asset_paths import AssetPaths, assert_upload_filename
from tests.helpers import DummyInstagramClient, test_settings
from tools.brand_mcp import BRAND_MCP_TOOL_NAMES, BrandMcp


def _png(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (12, 34, 56)).save(buffer, format="PNG")
    return buffer.getvalue()


def _app(tmp_path: Path, **overrides):
    settings = test_settings(tmp_path, **overrides)
    return create_app(settings, instagram_client=DummyInstagramClient()), settings  # type: ignore[arg-type]


def _auth(client: TestClient, email: str) -> tuple[dict[str, str], str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": "password12"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "password12"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    return headers, me.json()["user"]["id"]


def _upload(client: TestClient, headers: dict, *, name: str, content: bytes, mime: str, role: str, **fields: str) -> dict:
    response = client.post(
        "/api/v1/assets",
        headers=headers,
        files={"file": (name, content, mime)},
        data={"role": role, **fields},
    )
    return response


def test_user_cannot_retrieve_another_users_assets(tmp_path: Path) -> None:
    app, settings = _app(tmp_path)
    with TestClient(app) as client:
        headers_a, user_a = _auth(client, "a@example.com")
        headers_b, user_b = _auth(client, "b@example.com")

        uploaded = _upload(
            client,
            headers_a,
            name="logo.png",
            content=_png(),
            mime="image/png",
            role="logo_png",
        )
        assert uploaded.status_code == 200, uploaded.text
        asset = uploaded.json()["asset"]
        asset_id = asset["id"]
        assert asset["media_url"] == f"/api/v1/assets/{asset_id}/media"
        assert "storage_key" not in asset
        assert settings.media_root.as_posix() not in uploaded.text
        assert str(settings.media_root) not in uploaded.text

        brand = client.post(
            "/api/v1/brand",
            headers=headers_a,
            json={
                "company_name": "Yotto Atelier",
                "website": "https://yottolabs.com",
                "instagram_handle": "@yottolabs",
                "brand_colors": [{"name": "ink", "hex": "#112233"}],
                "fonts": [{"family": "Inter", "weight": "400"}],
                "logo_png_asset_id": asset_id,
                "guidelines": "Use the ink color on white.",
            },
        )
        assert brand.status_code == 200, brand.text
        assert brand.json()["brand"]["instagram_handle"] == "yottolabs"
        assert "storage_key" not in brand.text

        created = client.post(
            "/api/v1/products",
            headers=headers_a,
            json={
                "name": "Gold ring",
                "description": "Daily ring",
                "category": "jewelry",
                "price": "499.50",
                "sku": "RING-1",
                "is_active": True,
                "offer": "20% off",
            },
        )
        assert created.status_code == 200, created.text
        product_id = created.json()["product"]["id"]
        image = _upload(
            client,
            headers_a,
            name="ring.png",
            content=_png((80, 80)),
            mime="image/png",
            role="product_image",
            product_id=product_id,
        )
        assert image.status_code == 200, image.text
        client.post(
            "/api/v1/products",
            headers=headers_a,
            json={"name": "Plain stud", "sku": "STUD-1", "is_active": True},
        )
        client.post(
            "/api/v1/products",
            headers=headers_a,
            json={"name": "Old chain", "sku": "CHAIN-1", "is_active": False, "offer": "clearance"},
        )
        client.post(
            "/api/v1/products",
            headers=headers_b,
            json={"name": "Other ring", "sku": "RING-1", "is_active": True, "offer": "B only"},
        )

        listed = client.get("/api/v1/assets", headers=headers_b)
        assert listed.status_code == 200
        assert listed.json()["assets"] == []
        assert asset_id not in listed.text

        foreign = client.get(f"/api/v1/assets/{asset_id}", headers=headers_b)
        assert foreign.status_code == 404
        media = client.get(asset["media_url"], headers=headers_b)
        assert media.status_code == 404
        assert not media.content.startswith(b"\x89PNG")

        denied = client.delete(f"/api/v1/assets/{asset_id}", headers=headers_b)
        assert denied.status_code == 404
        still = client.get(asset["media_url"], headers=headers_a)
        assert still.status_code == 200
        assert still.content.startswith(b"\x89PNG")
        assert str(settings.media_root) not in still.headers.get("content-disposition", "")

        assert client.get(f"/api/v1/products/{product_id}", headers=headers_b).status_code == 404
        assert client.put(
            f"/api/v1/products/{product_id}",
            headers=headers_b,
            json={"name": "Stolen", "user_id": user_b},
        ).status_code == 404
        assert client.get("/api/v1/brand", headers=headers_b).json()["brand"] is None
        assert "Yotto Atelier" not in client.get("/api/v1/products", headers=headers_b).text

        owned = client.get(f"/api/v1/products/{product_id}", headers=headers_a)
        assert owned.status_code == 200
        assert owned.json()["product"]["name"] == "Gold ring"
        assert owned.json()["product"]["image"]["media_url"].startswith("/api/v1/assets/")
        assert "storage_key" not in owned.text

        stored = list((settings.media_root / "assets").rglob("asset_*"))
        assert stored
        for path in stored:
            assert user_a in path.parts
            assert user_b not in path.parts
            assert path.name not in client.get("/api/v1/assets", headers=headers_a).text

        with app.state.session_factory() as session:
            tools_b = BrandMcp(session, user_b, settings)
            assert tools_b.get_company_logo()["ok"] is False
            assert tools_b.get_product(product_id=product_id)["ok"] is False
            assert tools_b.get_product(sku="RING-1")["product"]["name"] == "Other ring"
            assert tools_b.get_product_image(product_id)["ok"] is False
            brand_assets = tools_b.get_brand_assets()
            assert brand_assets["brand"] is None
            assert brand_assets["assets"] == []
            assert "Yotto Atelier" not in json.dumps(brand_assets)
            with pytest.raises(AppError):
                tools_b.call("get_company_logo", {"user_id": user_a})
            with pytest.raises(AppError):
                tools_b.call("get_product", {"product_id": product_id, "path": "../secret"})

            tools_a = BrandMcp(session, user_a, settings)
            logo = tools_a.get_company_logo()
            assert logo["ok"] is True
            assert logo["logo"]["id"] == asset_id
            assert "storage_key" not in json.dumps(logo)
            guidelines = tools_a.get_brand_guidelines()
            assert guidelines["guidelines"][0]["body"] == "Use the ink color on white."
            assert tools_a.get_product_image(product_id)["ok"] is True
            names = {item["name"] for item in tools_a.get_active_products()["products"]}
            assert names == {"Gold ring", "Plain stud"}
            offers = {item["name"]: item["offer"] for item in tools_a.get_active_offers()["offers"]}
            assert offers == {"Gold ring": "20% off"}

    assert set(BRAND_MCP_TOOL_NAMES).isdisjoint(ALLOWED_TOOL_SET)


def test_owner_delete_removes_only_their_file(tmp_path: Path) -> None:
    app, settings = _app(tmp_path)
    with TestClient(app) as client:
        headers_a, user_a = _auth(client, "deleter@example.com")
        headers_b, user_b = _auth(client, "keeper@example.com")
        uploaded = _upload(client, headers_a, name="logo.png", content=_png(), mime="image/png", role="logo_png")
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["asset"]["id"]
        brand = client.post(
            "/api/v1/brand",
            headers=headers_a,
            json={"company_name": "Delete Me", "logo_png_asset_id": asset_id},
        )
        assert brand.status_code == 200, brand.text
        kept = _upload(
            client,
            headers_b,
            name="keep.png",
            content=_png((70, 70)),
            mime="image/png",
            role="product_image",
        )
        assert kept.status_code == 200, kept.text
        duplicate = client.post(
            "/api/v1/products",
            headers=headers_a,
            json={"name": "One", "sku": "SKU-1"},
        )
        assert duplicate.status_code == 200
        conflict = client.post(
            "/api/v1/products",
            headers=headers_a,
            json={"name": "Two", "sku": "SKU-1"},
        )
        assert conflict.status_code == 409

        removed = client.delete(f"/api/v1/assets/{asset_id}", headers=headers_a)
        assert removed.status_code == 200
        assert removed.json() == {"deleted": True}
        assert client.get(f"/api/v1/assets/{asset_id}", headers=headers_a).status_code == 404
        assert client.get(f"/api/v1/assets/{kept.json()['asset']['id']}/media", headers=headers_b).status_code == 200
        assert client.get("/api/v1/brand", headers=headers_a).json()["brand"]["logo_png"] is None
        assert list((settings.media_root / "assets" / user_a).rglob("asset_*")) == []
        assert list((settings.media_root / "assets" / user_b).rglob("asset_*"))


def test_asset_validation_rejects_unsafe_files(tmp_path: Path) -> None:
    app, settings = _app(tmp_path)
    with TestClient(app) as client:
        headers, _user = _auth(client, "files@example.com")
        client.cookies.clear()
        anon = client.post(
            "/api/v1/assets",
            files={"file": ("logo.png", _png(), "image/png")},
            data={"role": "logo_png"},
        )
        assert anon.status_code == 401
        cases = [
            ("../../etc/passwd.png", _png(), "image/png", "logo_png", {}, 400),
            ("logo.png", _png(), "image/png", "logo_png", {"scope": "brand/../../campaigns"}, 400),
            ("logo.png", b"GIF89a" + b"\x00" * 32, "image/gif", "logo_png", {}, 400),
            ("tiny.png", _png((8, 8)), "image/png", "logo_png", {}, 400),
            ("wide.png", _png((1600, 64)), "image/png", "logo_png", {}, 400),
            (
                "logo.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                "image/svg+xml",
                "logo_svg",
                {},
                400,
            ),
            (
                "logo.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg" href="https://evil.example/a.svg"></svg>',
                "image/svg+xml",
                "logo_svg",
                {},
                400,
            ),
            ("notes.pdf", b"%PDF-1.4\n/JavaScript (app.alert(1))\n%%EOF", "application/pdf", "guideline", {}, 400),
        ]
        for name, content, mime, role, fields, status in cases:
            response = _upload(client, headers, name=name, content=content, mime=mime, role=role, **fields)
            assert response.status_code == status, response.text
            assert str(settings.media_root) not in response.text
        assert list((settings.media_root / "assets").rglob("asset_*")) == []

        svg = _upload(
            client,
            headers,
            name="logo.svg",
            content=b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"></svg>',
            mime="image/svg+xml",
            role="logo_svg",
        )
        assert svg.status_code == 200, svg.text
        user_id = client.get("/api/v1/auth/me", headers=headers).json()["user"]["id"]
        assert list((settings.media_root / "assets" / user_id / "brand").glob("*.svg"))
        campaign = _upload(
            client,
            headers,
            name="poster.png",
            content=_png(),
            mime="image/png",
            role="campaign",
            scope="campaign",
        )
        assert campaign.status_code == 200, campaign.text
        assert list((settings.media_root / "assets" / user_id / "campaigns").glob("*.png"))

    limited, limited_settings = _app(tmp_path / "limited", max_image_bytes=32)
    with TestClient(limited) as client:
        headers, _user = _auth(client, "big@example.com")
        oversized = _upload(
            client,
            headers,
            name="logo.png",
            content=_png((128, 128)),
            mime="image/png",
            role="logo_png",
        )
        assert oversized.status_code == 413
        assert list((limited_settings.media_root / "assets").rglob("asset_*")) == []


def test_asset_paths_reject_traversal_and_foreign_keys(tmp_path: Path) -> None:
    paths = AssetPaths(tmp_path)
    with pytest.raises(AppError):
        assert_upload_filename(r"..\..\windows\system.ini")
    with pytest.raises(AppError):
        paths.resolve_storage_key("user-a", "assets/user-a/brand/../../etc/passwd")
    with pytest.raises(AppError):
        paths.allocate("../etc", "brand", "image/png")

    key, path = paths.allocate("user-a", "brand", "image/png")
    paths.write_bytes(path, _png())
    assert path.is_file()
    assert path.resolve().is_relative_to(paths.assets_root.resolve())
    assert key.startswith("assets/user-a/brand/")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    paths = AssetPaths(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    link_name = "asset_" + ("b" * 32) + ".png"
    link = paths.assets_root / "user-b" / "products" / link_name
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(AppError):
        paths.resolve_storage_key("user-b", f"assets/user-b/products/{link_name}")
    assert outside.read_bytes() == b"secret"


def test_delete_refuses_a_storage_key_outside_the_tenant(tmp_path: Path) -> None:
    app, settings = _app(tmp_path)
    with TestClient(app) as client:
        headers_a, user_a = _auth(client, "owner@example.com")
        headers_b, user_b = _auth(client, "other@example.com")
        owned = _upload(client, headers_a, name="logo.png", content=_png(), mime="image/png", role="logo_png")
        other = _upload(
            client,
            headers_b,
            name="ring.png",
            content=_png((72, 72)),
            mime="image/png",
            role="product_image",
        )
        assert owned.status_code == 200
        assert other.status_code == 200
        other_id = other.json()["asset"]["id"]
        with app.state.session_factory() as session:
            row = session.get(BusinessAsset, owned.json()["asset"]["id"])
            foreign = session.get(BusinessAsset, other_id)
            assert row is not None and foreign is not None
            foreign_key = foreign.storage_key
            row.storage_key = foreign_key
            session.commit()
        denied = client.delete(f"/api/v1/assets/{owned.json()['asset']['id']}", headers=headers_a)
        assert denied.status_code == 400
        with app.state.session_factory() as session:
            assert session.get(BusinessAsset, other_id) is not None
        media = client.get(f"/api/v1/assets/{other_id}/media", headers=headers_b)
        assert media.status_code == 200
        assert media.content.startswith(b"\x89PNG")
        assert user_a != user_b
