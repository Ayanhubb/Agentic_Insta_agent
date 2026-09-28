/** Registered options the agent builder may bind. Names only. No credentials. */

import type { PlatformAgent, AgentVersionView } from "../types/agents";

export const LLM_PROVIDERS = [
  { id: "deepseek", label: "DeepSeek" },
  { id: "openai", label: "OpenAI" },
  { id: "mock", label: "Mock" },
];

export const IMAGE_PROVIDERS = [
  { id: "openai", label: "OpenAI" },
  { id: "mock", label: "Mock" },
];

export const VISION_PROVIDERS = [
  { id: "deepseek", label: "DeepSeek" },
  { id: "mock", label: "Mock" },
];

export const MCP_TOOLS = [
  "get_business_profile",
  "get_brand_guidelines",
  "get_company_logo",
  "get_product",
  "get_product_image",
  "get_active_offers",
  "get_upcoming_festivals",
  "get_festival_details",
  "get_content_rules",
  "get_account_summary",
  "get_recent_media",
  "get_account_insights",
  "get_top_content",
  "get_content_performance",
  "get_publishing_history",
  "get_current_trends",
  "get_trend_evidence",
  "get_regional_trends",
  "get_festival_opportunities",
  "get_business_opportunities",
  "get_content_opportunities",
  "get_latest_trend_report",
].map((name) => ({ id: name, label: name.replaceAll("_", " ") }));

export const KNOWLEDGE_SOURCES = [
  { id: "business", label: "Business profile" },
  { id: "brand", label: "Brand guidelines" },
  { id: "product", label: "Products and offers" },
  { id: "document", label: "Documents" },
  { id: "festival", label: "Festivals" },
  { id: "account", label: "Instagram account" },
  { id: "trend", label: "Trends" },
  { id: "content_rules", label: "Content rules" },
];

export const ASSET_ROLES = [
  { id: "company_logo", label: "Company logo" },
  { id: "product_image", label: "Product image" },
  { id: "brand_guide", label: "Brand guide" },
  { id: "campaign", label: "Campaign" },
  { id: "document", label: "Document" },
  { id: "reference_image", label: "Reference image" },
  { id: "agent_asset", label: "Agent asset" },
];

export const TRIGGERS = [
  { id: "reference.run", label: "Reference run" },
  { id: "manual", label: "Manual" },
  { id: "http.generation", label: "Studio generation" },
  { id: "http.approve", label: "Studio approval" },
  { id: "automation.run_now", label: "Run now" },
  { id: "scheduler.daily", label: "Daily scheduler" },
];

export const APPROVAL_POLICY = {
  id: "instagram.still_image",
  label: "Instagram still image",
  summary: "The existing still-image gates stay in place. This form cannot remove one.",
  gates: [
    "image_qa_failed",
    "provider_result_malformed",
    "product_reference_missing",
    "offer_cannot_be_verified",
    "required_logo_missing",
    "festival_context_invalid",
    "tenant_ownership_failed",
  ],
};

export const REFERENCE_WORKFLOW = {
  id: "instagram.reference",
  label: "Instagram reference",
  summary: "The studio's existing order. Publish is one closed step inside the Instagram Agent.",
  steps: [
    { position: 1, step_key: "load_business_context", kind: "load_context", side_effect: "none" },
    { position: 2, step_key: "load_brand_context", kind: "load_context", side_effect: "none" },
    { position: 3, step_key: "load_product_context", kind: "load_context", side_effect: "none" },
    { position: 4, step_key: "load_festival_context", kind: "load_context", side_effect: "none" },
    { position: 5, step_key: "load_instagram_intelligence", kind: "load_context", side_effect: "none" },
    { position: 6, step_key: "load_trend_evidence", kind: "load_context", side_effect: "none" },
    { position: 7, step_key: "content_planning", kind: "reason", side_effect: "provider_call" },
    { position: 8, step_key: "image_generation", kind: "reason", side_effect: "provider_call" },
    { position: 9, step_key: "vision_qa", kind: "reason", side_effect: "provider_call" },
    { position: 10, step_key: "approval_policy", kind: "decide_approval", side_effect: "none" },
    { position: 11, step_key: "instagram_agent_publication", kind: "effect", side_effect: "external_effect" },
    { position: 12, step_key: "meta_verification", kind: "validate", side_effect: "none" },
    { position: 13, step_key: "persist_result", kind: "store", side_effect: "local_write" },
  ],
};

export function currentVersion(agent: PlatformAgent): AgentVersionView | null {
  const number = agent.draft_version_number ?? agent.published_version_number;
  return agent.versions.find((item) => item.version_number === number) ?? agent.versions[0] ?? null;
}

export function slugKey(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  if (!slug) return "";
  return /^[a-z]/.test(slug) ? slug : `agent_${slug}`.slice(0, 64);
}
