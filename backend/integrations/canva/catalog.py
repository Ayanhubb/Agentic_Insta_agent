"""Map application capabilities onto Canva MCP tools that were actually discovered.

The names below are Canva's published tool names. They are candidates only.
A capability is offered, and a tool is called, only after `tools/list` returns
that name for the signed-in user's connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.errors import AppError, ErrorCode

CREATE_DESIGN = "create-design"
GENERATE_DESIGN = "generate-design"
CREATE_FROM_CANDIDATE = "create-design-from-candidate"
START_EDIT = "start-editing-transaction"
PERFORM_EDIT = "perform-editing-operations"
COMMIT_EDIT = "commit-editing-transaction"
CANCEL_EDIT = "cancel-editing-transaction"
SEARCH_DESIGNS = "search-designs"
EXPORT_DESIGN = "export-design"
EXPORT_FORMATS = "get-export-formats"
LIST_BRAND_KITS = "list-brand-kits"
SEARCH_BRAND_TEMPLATES = "search-brand-templates"
GET_ASSETS = "get-assets"

EDIT_TOOLS = (START_EDIT, PERFORM_EDIT, COMMIT_EDIT)
BRAND_TOOLS = (LIST_BRAND_KITS, SEARCH_BRAND_TEMPLATES, GET_ASSETS)


@dataclass(frozen=True)
class DiscoveredTool:
    name: str
    input_schema: dict[str, Any]
    description: str = ""


def available_capabilities(tools: dict[str, DiscoveredTool]) -> list[str]:
    names = set(tools)
    capabilities: list[str] = []
    if CREATE_DESIGN in names or (GENERATE_DESIGN in names and CREATE_FROM_CANDIDATE in names):
        capabilities.append("create_design")
    if all(name in names for name in EDIT_TOOLS):
        capabilities.append("edit_design")
    if SEARCH_DESIGNS in names:
        capabilities.append("search_designs")
    if any(name in names for name in BRAND_TOOLS):
        capabilities.append("get_brand_assets")
    if EXPORT_DESIGN in names:
        capabilities.append("export_design")
    return capabilities


def bind_schema(schema: dict[str, Any] | None, values: dict[str, Any]) -> dict[str, Any]:
    """Keep only arguments the discovered input schema declares."""
    properties = (schema or {}).get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        raise AppError(
            ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
            "Canva did not publish an input schema for this tool.",
            http_status=409,
        )
    bound: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or key not in properties:
            continue
        bound[key] = value
    required = [name for name in (schema or {}).get("required") or [] if isinstance(name, str)]
    missing = [name for name in required if name not in bound]
    if missing:
        raise AppError(
            ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
            "The discovered Canva tool schema requires arguments this capability cannot supply.",
            http_status=409,
            details={"missing": missing},
        )
    return bound


def shape_format(schema: dict[str, Any] | None, format_name: str) -> Any:
    prop = ((schema or {}).get("properties") or {}).get("format")
    if not isinstance(prop, dict):
        return None
    if prop.get("type") == "object" or isinstance(prop.get("properties"), dict):
        return {"type": format_name}
    return format_name


def clean_operations(schema: dict[str, Any] | None, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from services.logging import is_secret_key

    item_props = (
        (((schema or {}).get("properties") or {}).get("operations") or {}).get("items") or {}
    ).get("properties") or {}
    cleaned: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        raw: dict[str, Any] = {}
        for key, value in operation.items():
            if value is None or is_secret_key(str(key)):
                continue
            if item_props and key not in item_props:
                continue
            raw[str(key)] = value
        if raw:
            cleaned.append(raw)
    return cleaned
