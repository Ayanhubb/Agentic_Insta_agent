"""Registered options an agent builder may bind. Names only. No credentials."""

from __future__ import annotations

from typing import Any

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS, MCP_TOOL_ALLOWLIST
from backend.platform.assets.catalog import AssetRole
from backend.platform.builder.secrets import looks_like_secret
from backend.platform.knowledge.catalog import KnowledgeSource
from config import Settings
from instagram_reference.definition import (
    AGENT_KEY,
    DISPLAY_NAME,
    POLICY_ID,
    TRIGGER_KEY,
    WORKFLOW_KEY,
    reference_workflow,
)

SYSTEM_AGENT_ID = AGENT_KEY

LLM_PROVIDERS = (
    {"id": "deepseek", "label": "DeepSeek"},
    {"id": "openai", "label": "OpenAI"},
    {"id": "mock", "label": "Mock"},
)
IMAGE_PROVIDERS = (
    {"id": "openai", "label": "OpenAI"},
    {"id": "mock", "label": "Mock"},
)
LLM_MODELS = {
    "deepseek": ("deepseek-flash", "deepseek-chat"),
    "openai": ("gpt-4o-mini", "gpt-4o"),
    "mock": ("mock",),
}
IMAGE_MODELS = {
    "openai": ("gpt-image-1",),
    "mock": ("mock",),
}

KNOWLEDGE_LABELS = {
    KnowledgeSource.BUSINESS.value: "Business profile",
    KnowledgeSource.BRAND.value: "Brand guidelines",
    KnowledgeSource.PRODUCT.value: "Products and offers",
    KnowledgeSource.DOCUMENT.value: "Documents",
    KnowledgeSource.FESTIVAL.value: "Festivals",
    KnowledgeSource.ACCOUNT.value: "Instagram account",
    KnowledgeSource.TREND.value: "Trends",
    KnowledgeSource.CONTENT_RULES.value: "Content rules",
}
ASSET_LABELS = {
    AssetRole.COMPANY_LOGO.value: "Company logo",
    AssetRole.PRODUCT_IMAGE.value: "Product image",
    AssetRole.BRAND_GUIDE.value: "Brand guide",
    AssetRole.CAMPAIGN.value: "Campaign",
    AssetRole.DOCUMENT.value: "Document",
    AssetRole.REFERENCE_IMAGE.value: "Reference image",
    AssetRole.AGENT_ASSET.value: "Agent asset",
}

_STUDIO_WORKFLOWS = (
    {
        "id": "studio.generate",
        "label": "Studio generation",
        "summary": "Load context, plan, render, and store a pending image. This workflow does not publish.",
        "selectable": True,
        "steps": (
            {"position": 1, "step_key": "load_context", "kind": "load_context", "side_effect": "none"},
            {"position": 2, "step_key": "reason", "kind": "reason", "side_effect": "provider_call"},
            {"position": 3, "step_key": "validate_plan", "kind": "validate", "side_effect": "none"},
            {"position": 4, "step_key": "render_image", "kind": "reason", "side_effect": "provider_call"},
            {"position": 5, "step_key": "vision_qa", "kind": "validate", "side_effect": "provider_call"},
            {"position": 6, "step_key": "store_pending", "kind": "store", "side_effect": "local_write"},
        ),
    },
    {
        "id": "studio.approve",
        "label": "Studio approval",
        "summary": "A person approves a pending image. Publication still goes through the Instagram Agent.",
        "selectable": True,
        "steps": (
            {"position": 1, "step_key": "human_decision", "kind": "human_decision", "side_effect": "local_write"},
            {"position": 2, "step_key": "enqueue_publish", "kind": "effect", "side_effect": "external_effect"},
        ),
    },
    {
        "id": "scheduler.campaign",
        "label": "Scheduler campaign",
        "summary": "The existing daily and festival campaign path. Publish remains the Instagram Agent handoff.",
        "selectable": True,
        "steps": (
            {"position": 1, "step_key": "load_context", "kind": "load_context", "side_effect": "none"},
            {"position": 2, "step_key": "content_planning", "kind": "reason", "side_effect": "provider_call"},
            {"position": 3, "step_key": "decide_approval", "kind": "decide_approval", "side_effect": "none"},
        ),
    },
    {
        "id": "trend.cycle",
        "label": "Trend cycle",
        "summary": "Read stored trend evidence and produce recommendations. It does not publish.",
        "selectable": True,
        "steps": (
            {"position": 1, "step_key": "load_trend_evidence", "kind": "load_context", "side_effect": "none"},
            {"position": 2, "step_key": "analyze", "kind": "reason", "side_effect": "provider_call"},
            {"position": 3, "step_key": "store_report", "kind": "store", "side_effect": "local_write"},
        ),
    },
)

TRIGGERS = (
    {"id": "manual", "label": "Manual", "selectable": True},
    {"id": "http.generation", "label": "Studio generation", "selectable": True},
    {"id": "http.approve", "label": "Studio approval", "selectable": True},
    {"id": "automation.run_now", "label": "Run now", "selectable": True},
    {"id": "scheduler.daily", "label": "Daily scheduler", "selectable": True},
    {"id": TRIGGER_KEY, "label": "Reference run", "selectable": False},
)

POLICIES = (
    {
        "id": "human.required",
        "label": "Human approval",
        "summary": "Automatic publish stays off until a person approves.",
        "gates": ("human_approval_required",),
    },
    {
        "id": "instagram.still_image",
        "label": "Instagram still image",
        "summary": "The existing still-image gates stay in place. This form cannot remove one.",
        "gates": (
            "image_qa_failed",
            "provider_result_malformed",
            "product_reference_missing",
            "offer_cannot_be_verified",
            "required_logo_missing",
            "festival_context_invalid",
            "tenant_ownership_failed",
        ),
    },
)


def _safe_name(value: str) -> str:
    text = value.strip()
    if not text or looks_like_secret(text) or len(text) > 128:
        return ""
    if any(character.isspace() for character in text):
        return ""
    return text


def _model_rows(table: dict[str, tuple[str, ...]], extra: tuple[str, str] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for provider, names in table.items():
        for name in names:
            rows.append({"provider": provider, "id": name, "label": name})
            seen.add((provider, name))
    if extra is not None and extra not in seen and extra[0] in table:
        rows.append({"provider": extra[0], "id": extra[1], "label": extra[1]})
    return rows


def _configured_model(settings: Settings, provider: str, *, image: bool) -> str:
    if provider == "mock":
        return "mock"
    if image:
        return _safe_name(settings.image_model or settings.openai_image_model or "")
    if provider == "deepseek":
        return _safe_name(settings.deepseek_model or "")
    return _safe_name(settings.llm_model or "")


def reference_steps() -> list[dict[str, Any]]:
    workflow = reference_workflow()
    return [
        {
            "position": step.position,
            "step_key": step.step_key,
            "kind": step.kind.value,
            "side_effect": step.side_effect.value,
        }
        for step in workflow.steps
    ]


def workflows() -> list[dict[str, Any]]:
    reference = {
        "id": WORKFLOW_KEY,
        "label": "Instagram reference",
        "summary": "The studio's existing order. Publish is one closed step inside the Instagram Agent.",
        "selectable": False,
        "steps": reference_steps(),
    }
    rows = [reference]
    for item in _STUDIO_WORKFLOWS:
        rows.append({**item, "steps": [dict(step) for step in item["steps"]]})
    return rows


def public_catalog(settings: Settings) -> dict[str, Any]:
    """Options the builder may show. Provider keys are not copied."""

    llm_extra = None
    image_extra = None
    reasoning = _safe_name(settings.llm_provider).lower()
    image = _safe_name(settings.image_provider).lower()
    if reasoning in LLM_MODELS:
        model = _configured_model(settings, reasoning, image=False)
        if model and model not in LLM_MODELS[reasoning]:
            llm_extra = (reasoning, model)
    if image in IMAGE_MODELS:
        model = _configured_model(settings, image, image=True)
        if model and model not in IMAGE_MODELS[image]:
            image_extra = (image, model)
    tools = [
        {"name": name, "label": name.replace("_", " ")}
        for name in sorted(MCP_TOOL_ALLOWLIST)
        if name not in INSTAGRAM_PUBLISHING_TOOLS
    ]
    return {
        "llm_providers": [
            {
                "id": item["id"],
                "label": item["label"],
                "configured": _provider_configured(settings, item["id"]),
            }
            for item in LLM_PROVIDERS
        ],
        "llm_models": _model_rows(LLM_MODELS, llm_extra),
        "image_providers": [
            {
                "id": item["id"],
                "label": item["label"],
                "configured": _provider_configured(settings, item["id"]),
            }
            for item in IMAGE_PROVIDERS
        ],
        "image_models": _model_rows(IMAGE_MODELS, image_extra),
        "mcp_tools": tools,
        "knowledge_sources": [
            {"id": source.value, "label": KNOWLEDGE_LABELS[source.value]}
            for source in KnowledgeSource
        ],
        "asset_roles": [
            {"id": role.value, "label": ASSET_LABELS[role.value]} for role in AssetRole
        ],
        "workflows": workflows(),
        "triggers": [dict(item) for item in TRIGGERS],
        "approval_policies": [
            {**item, "gates": list(item["gates"])} for item in POLICIES
        ],
    }


def _provider_configured(settings: Settings, provider: str) -> bool:
    if provider == "mock":
        return True
    if provider == "deepseek":
        return bool(settings.deepseek_configured)
    if provider == "openai":
        return bool(settings.openai_configured)
    return False


def default_models(settings: Settings) -> dict[str, str]:
    catalog = public_catalog(settings)
    reasoning = _safe_name(settings.llm_provider).lower()
    if reasoning not in {item["id"] for item in catalog["llm_providers"]}:
        reasoning = "mock"
    llm_choices = [row["id"] for row in catalog["llm_models"] if row["provider"] == reasoning]
    preferred = _configured_model(settings, reasoning, image=False)
    llm_model = preferred if preferred in llm_choices else llm_choices[0]
    image = _safe_name(settings.image_provider).lower()
    if image not in {item["id"] for item in catalog["image_providers"]}:
        image = "mock"
    image_choices = [row["id"] for row in catalog["image_models"] if row["provider"] == image]
    preferred_image = _configured_model(settings, image, image=True)
    image_model = preferred_image if preferred_image in image_choices else image_choices[0]
    return {
        "llm_provider": reasoning,
        "llm_model": llm_model,
        "image_provider": image,
        "image_model": image_model,
    }


def system_agent_fields(settings: Settings) -> dict[str, Any]:
    from instagram_reference.definition import reference_version

    version = reference_version()
    models = default_models(settings)
    return {
        "id": SYSTEM_AGENT_ID,
        "name": DISPLAY_NAME,
        "description": "The Instagram studio is this agent's interface. Configuration here is read-only.",
        "owner": "system",
        "status": "active",
        "read_only": True,
        "llm_provider": models["llm_provider"],
        "llm_model": models["llm_model"],
        "image_provider": models["image_provider"],
        "image_model": models["image_model"],
        "mcp_tools": sorted(name for name in version.tool_grants if name not in INSTAGRAM_PUBLISHING_TOOLS),
        "assets": sorted(version.asset_roles),
        "knowledge_sources": sorted(version.knowledge_sources),
        "workflow_id": WORKFLOW_KEY,
        "triggers": [TRIGGER_KEY],
        "approval_policy_id": POLICY_ID,
        "published_version_number": 1,
    }
