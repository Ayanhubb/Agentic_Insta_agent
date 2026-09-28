"""Agent builder API keeps credentials off the response."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings

ROOT = Path(__file__).resolve().parents[1]


def _user(user_id: str):
    async def provider(_request):
        return SimpleNamespace(id=user_id, must_change_password=False, is_active=True, is_admin=False)

    return provider


def test_agent_builder_requires_auth_and_hides_secrets(tmp_settings: Settings) -> None:
    secret = "sk-openai-should-not-leak"
    settings = tmp_settings.model_copy(
        update={
            "openai_api_key": secret,
            "llm_model": "sk-model-should-not-leak",
            "deepseek_api_key": "sk-deepseek-should-not-leak",
        }
    )
    anonymous = create_app(settings)
    with TestClient(anonymous) as client:
        denied = client.get("/api/v1/agents")
        assert denied.status_code == 401

    app = create_app(settings, current_user_provider=_user("user-a"))
    with TestClient(app) as client:
        listed = client.get("/api/v1/agents")
        assert listed.status_code == 200
        assert secret not in listed.text
        assert "sk-model-should-not-leak" not in listed.text
        assert "openai_api_key" not in listed.text
        assert tmp_settings.jwt_secret not in listed.text
        assert tmp_settings.token_encryption_key not in listed.text
        reference = next(item for item in listed.json()["agents"] if item["key"] == "instagram_marketing")
        assert reference["writable"] is False
        assert reference["display_name"] == "Instagram Marketing Agent"

        leaked = client.post(
            "/api/v1/agents",
            json={"key": "window_agent", "display_name": "Window", "api_key": secret},
        )
        assert leaked.status_code == 400
        assert secret not in leaked.text

        created = client.post(
            "/api/v1/agents",
            json={"key": "window_agent", "display_name": "Window", "description": "Shop"},
        )
        assert created.status_code == 200
        agent = created.json()["agent"]
        assert agent["owner"] == "user"
        assert agent["writable"] is True
        assert agent["description"] == "Shop"
        assert secret not in created.text
        tools = agent["versions"][0]["tool_names"]
        assert "publish_instagram_media" not in tools

        denied_tools = client.patch(
            "/api/v1/agents/window_agent",
            json={"tool_names": ["publish_instagram_media"]},
        )
        assert denied_tools.status_code == 400
        assert "publish_instagram_media" not in client.get("/api/v1/agents/window_agent").json()["agent"]["versions"][0]["tool_names"]

        system = client.patch(
            "/api/v1/agents/instagram_marketing",
            json={"display_name": "Renamed"},
        )
        assert system.status_code == 403


def test_agent_builder_is_owner_scoped(tmp_settings: Settings) -> None:
    current = {"id": "user-a"}

    async def provider(_request):
        return SimpleNamespace(
            id=current["id"],
            must_change_password=False,
            is_active=True,
            is_admin=False,
        )

    app = create_app(tmp_settings, current_user_provider=provider)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/agents",
            json={"key": "owner_agent", "display_name": "Owner agent", "description": ""},
        )
        assert created.status_code == 200
        current["id"] = "user-b"
        missing = client.get("/api/v1/agents/owner_agent")
        assert missing.status_code == 404
        assert "Owner agent" not in missing.text


def test_versions_publish_rollback_and_test_runs_do_not_publish(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    async def _enqueue(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("test execution must not enqueue publication")

    monkeypatch.setattr("agent.instagram_tasks.enqueue_instagram_publication", _enqueue)

    def _reject_graph(self: object, *args: object, **kwargs: object) -> None:
        del self, args, kwargs
        raise AssertionError("Meta Graph client must not be constructed")

    monkeypatch.setattr("tools.instagram_media.InstagramGraphClient.__init__", _reject_graph)

    current = {"id": "user-a"}

    async def provider(_request):
        return SimpleNamespace(
            id=current["id"],
            must_change_password=False,
            is_active=True,
            is_admin=False,
        )

    app = create_app(tmp_settings, current_user_provider=provider)
    with TestClient(app) as client:
        created = client.post("/api/v1/agents", json={"key": "shop_voice", "display_name": "Shop voice"})
        assert created.status_code == 200
        agent = created.json()["agent"]
        assert agent["published_version_number"] is None
        assert agent["draft_version_number"] == 1
        assert client.post("/api/v1/agents/shop_voice/validate").json()["valid"] is True

        code = client.patch("/api/v1/agents/shop_voice", json={"handler": "os.system"})
        assert code.status_code == 400
        secret_model = client.patch(
            "/api/v1/agents/shop_voice",
            json={"models": [{"role": "content_planning", "implementation": "deepseek", "model": "sk-abcdefghij"}]},
        )
        assert secret_model.status_code == 400
        image = client.patch(
            "/api/v1/agents/shop_voice",
            json={"models": [{"role": "image_generation", "implementation": "deepseek", "model": ""}]},
        )
        assert image.status_code == 400

        narrowed = client.patch("/api/v1/agents/shop_voice", json={"knowledge_sources": ["brand"]})
        assert narrowed.status_code == 200
        invalid = client.post("/api/v1/agents/shop_voice/validate")
        assert invalid.json()["valid"] is False
        client.patch(
            "/api/v1/agents/shop_voice",
            json={"knowledge_sources": ["business", "brand", "product", "festival", "account", "trend"]},
        )

        draft_run = client.post("/api/v1/agents/shop_voice/execute", json={"payload": {"publish": True}})
        assert draft_run.status_code == 200
        draft_steps = {step["step_key"]: step["status"] for step in draft_run.json()["execution"]["steps"]}
        assert draft_steps["instagram_agent_publication"] == "blocked"
        assert draft_run.json()["execution"]["mode"] == "test"

        published = client.post("/api/v1/agents/shop_voice/publish")
        assert published.status_code == 200
        assert published.json()["agent"]["published_version_number"] == 1
        assert client.patch("/api/v1/agents/shop_voice", json={"display_name": "Changed"}).status_code == 409

        opened = client.post("/api/v1/agents/shop_voice/versions")
        assert opened.json()["agent"]["draft_version_number"] == 2
        client.patch("/api/v1/agents/shop_voice", json={"tool_names": ["get_business_profile"]})
        second = client.post("/api/v1/agents/shop_voice/publish")
        versions = {item["version_number"]: item for item in second.json()["agent"]["versions"]}
        assert "get_brand_guidelines" in versions[1]["tool_names"]
        assert versions[2]["tool_names"] == ["get_business_profile"]
        rolled = client.post("/api/v1/agents/shop_voice/rollback", json={"version_number": 1})
        assert rolled.json()["agent"]["published_version_number"] == 1

        clean = client.post(
            "/api/v1/agents/shop_voice/execute",
            json={
                "payload": {
                    "publish": True,
                    "mode": "DAILY",
                    "ownership_ok": True,
                    "automation": {"auto_daily_publish": True},
                    "profile": {"business_name": "Shop"},
                }
            },
        )
        assert clean.status_code == 200
        assert clean.json()["execution"]["error"]["code"] == "APPROVAL_DENIED"

        assert client.post("/api/v1/agents/shop_voice/disable").status_code == 200
        assert client.post("/api/v1/agents/shop_voice/execute", json={}).status_code == 409
        assert client.post("/api/v1/agents/shop_voice/publish").status_code == 200

        cloned = client.post(
            "/api/v1/agents/instagram_marketing/clone",
            json={"key": "studio_copy", "display_name": "Copy"},
        )
        assert cloned.status_code == 200
        assert cloned.json()["agent"]["owner"] == "user"
        assert cloned.json()["agent"]["published_version_number"] is None
        assert client.post("/api/v1/agents/instagram_marketing/disable").status_code == 403
        assert client.delete("/api/v1/agents/instagram_marketing").status_code == 403

        client.delete("/api/v1/agents/shop_voice")
        assert client.get("/api/v1/agents/shop_voice").json()["agent"]["status"] == "retired"
        assert all(item["key"] != "shop_voice" for item in client.get("/api/v1/agents").json()["agents"])
        assert client.post("/api/v1/agents/shop_voice/execute", json={}).status_code == 404

        current["id"] = "user-b"
        assert client.get("/api/v1/agents/studio_copy").status_code == 404
        assert client.get("/api/v1/agents/shop_voice/executions").status_code == 404
    assert calls == []


def test_builder_does_not_replace_meta_publishing_routes() -> None:
    routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    agent_routes = (ROOT / "api" / "agent_routes.py").read_text(encoding="utf-8")
    assert '@router.post("/instagram/publish")' in routes
    assert "enqueue_instagram_publication" not in agent_routes
    assert "InstagramGraphClient" not in agent_routes
