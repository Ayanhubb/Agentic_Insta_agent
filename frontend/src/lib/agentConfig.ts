import { ApiError } from "../services/api/errors";
import { isSecretKey } from "./providerStatus";

const SECRET_VALUE = /sk-|sk-proj-|EAAB|Bearer\s+|eyJ[A-Za-z0-9_-]{8,}\./i;

const BLOCKED_TOOLS = new Set([
  "validate_image",
  "prepare_image",
  "upload_image",
  "create_instagram_media",
  "publish_instagram_media",
  "verify_publication",
  "media_publish",
  "instagram_publish",
  "publish",
  "post_to_instagram",
  "create_media",
  "publish_media",
  "enqueue_instagram_publication",
]);

function discarded(): never {
  throw new ApiError("The response included a credential and was discarded.", {
    status: 422,
    code: "SECRET_RESPONSE",
  });
}

function isBlockedTool(value: unknown): boolean {
  if (typeof value === "string") return BLOCKED_TOOLS.has(value);
  if (value && typeof value === "object" && "name" in value) {
    return BLOCKED_TOOLS.has(String((value as { name: unknown }).name));
  }
  return false;
}

function stripBlocked(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(stripBlocked);
    return;
  }
  if (!value || typeof value !== "object") return;
  const record = value as Record<string, unknown>;
  if (Array.isArray(record.mcp_tools)) {
    record.mcp_tools = record.mcp_tools.filter((item) => !isBlockedTool(item));
  }
  if (Array.isArray(record.tool_names)) {
    record.tool_names = record.tool_names.filter((item) => !isBlockedTool(item));
  }
  Object.values(record).forEach(stripBlocked);
}

function rejectSecrets(value: unknown): void {
  if (typeof value === "string") {
    if (SECRET_VALUE.test(value)) discarded();
    return;
  }
  if (Array.isArray(value)) {
    value.forEach(rejectSecrets);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (isSecretKey(key)) discarded();
    rejectSecrets(item);
  }
}

/** Copy a builder payload and refuse to return credentials or publish-tool grants. */
export function receivePublic<T>(payload: unknown): T {
  const copy = structuredClone(payload);
  stripBlocked(copy);
  rejectSecrets(copy);
  return copy as T;
}
