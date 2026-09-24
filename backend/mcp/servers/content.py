"""Content-rule tool. Vocabulary and diversity limits come from the Content Agent models."""

from __future__ import annotations

from typing import Any

from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext
from models.content import ContentMode, ContentType


def content_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_content_rules(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        from agent.content_agent import DiversityPolicy

        policy = DiversityPolicy()
        return {
            "content_types": [item.value for item in ContentType],
            "modes": [item.value for item in ContentMode],
            "publishing": {
                "available": False,
                "user_prompt_auto_publish": False,
            },
            "diversity": {
                "backend": "token_overlap_v1",
                "prompt_threshold": policy.prompt_threshold,
                "max_same_type": policy.max_same_type,
                "user_prompt": "advisory",
                "daily_and_festival": "blocking",
            },
            "festival": {
                "must_mention_business": True,
            },
            "automation": gateway.automation(tenant.tenant_id),
        }

    return [
        MCPTool(
            name="get_content_rules",
            description="Read content types, diversity limits, and this tenant's automation settings.",
            input_schema=object_schema(),
            server="content",
            handler=get_content_rules,
        )
    ]
