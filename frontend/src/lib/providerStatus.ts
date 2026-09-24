const SECRET_KEY =
  /(api[_-]?key|access[_-]?token|refresh[_-]?token|jwt|encryption[_-]?key|password|secret|authorization|bearer|canva_mcp_token)/i;

const SECRET_VALUE = /sk-|EAAB|eyJ[A-Za-z0-9_-]{8,}|Bearer\s+/;

export function isSecretKey(key: string): boolean {
  return SECRET_KEY.test(key);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function boolish(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === "connected" || value === "Connected" || value === "configured" || value === "on") return true;
  if (
    value === "not_configured" ||
    value === "not_connected" ||
    value === "Not configured" ||
    value === "Not connected" ||
    value === "off"
  ) {
    return false;
  }
  return null;
}

function pick(record: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (isSecretKey(key)) continue;
    if (key in record) return record[key];
  }
  return undefined;
}

function safeName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > 40 || SECRET_VALUE.test(text) || isSecretKey(text)) return null;
  return text;
}

export interface PublicProviderStatus {
  deepseek: "Connected" | "Not configured";
  openai: "Connected" | "Not configured";
  canva: "Connected" | "Not connected";
  mcp: "On" | "Off" | "Not available";
  llmProvider: string | null;
  imageProvider: string | null;
  visionProvider: string | null;
}

export function normalizeProviderStatus(payload: unknown): PublicProviderStatus {
  const root = asRecord(payload) ?? {};
  const nested = asRecord(root.ai) ?? asRecord(root.providers);
  const ai = nested ?? root;
  const deepseekOn = boolish(pick(ai, ["deepseek_configured", "deepseek"])) === true;
  const openaiOn = boolish(pick(ai, ["openai_configured", "openai"])) === true;
  const configured = boolish(pick(ai, ["canva_configured", "canva_connected", "canva"]));
  const canvaOn = configured === true;
  const mcpRaw = boolish(pick(ai, ["mcp_enabled", "mcp"]));
  return {
    deepseek: deepseekOn ? "Connected" : "Not configured",
    openai: openaiOn ? "Connected" : "Not configured",
    canva: canvaOn ? "Connected" : "Not connected",
    mcp: mcpRaw === null ? "Not available" : mcpRaw ? "On" : "Off",
    llmProvider: safeName(pick(ai, ["llm_provider"])),
    imageProvider: safeName(pick(ai, ["image_provider"])),
    visionProvider: safeName(pick(ai, ["vision_provider"])),
  };
}

export interface PublicMcpTool {
  name: string;
  available: boolean;
}

export interface PublicMcpStatus {
  enabled: "On" | "Off" | "Not available";
  canva: "Connected" | "Not connected";
  tools: PublicMcpTool[];
}

const TOOL_NAME = /^[a-z][a-z0-9_.-]{0,64}$/;

export function normalizeMcpStatus(payload: unknown): PublicMcpStatus {
  const root = asRecord(payload) ?? {};
  const enabledRaw = boolish(pick(root, ["enabled", "mcp_enabled"]));
  const canvaOn = boolish(pick(root, ["canva_connected", "canva_enabled", "canva"])) === true;
  const rawTools = Array.isArray(root.tools) ? root.tools : [];
  const tools: PublicMcpTool[] = [];
  for (const item of rawTools) {
    const record = asRecord(item);
    if (!record) continue;
    const name = safeName(record.name);
    if (!name || !TOOL_NAME.test(name)) continue;
    tools.push({ name, available: record.available === true || record.enabled === true });
  }
  return {
    enabled: enabledRaw === null ? "Not available" : enabledRaw ? "On" : "Off",
    canva: canvaOn ? "Connected" : "Not connected",
    tools,
  };
}
