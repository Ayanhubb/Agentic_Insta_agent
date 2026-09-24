import { normalizeMcpStatus, normalizeProviderStatus, type PublicMcpStatus, type PublicProviderStatus } from "../../lib/providerStatus";
import { request } from "./client";
import { API } from "./endpoints";
import { isNotImplemented } from "./errors";
import { settingsApi } from "./settings";

const UNAVAILABLE_AI: PublicProviderStatus = {
  deepseek: "Not configured",
  openai: "Not configured",
  canva: "Not connected",
  mcp: "Not available",
  llmProvider: null,
  imageProvider: null,
  visionProvider: null,
};

export const providersApi = {
  async ai(): Promise<PublicProviderStatus> {
    try {
      const payload = await request<unknown>(API.aiStatus);
      return normalizeProviderStatus(payload);
    } catch (error) {
      if (!isNotImplemented(error)) throw error;
    }
    try {
      const settings = await settingsApi.get();
      return normalizeProviderStatus(settings);
    } catch (error) {
      if (!isNotImplemented(error)) throw error;
      return UNAVAILABLE_AI;
    }
  },

  async mcp(): Promise<PublicMcpStatus> {
    try {
      const payload = await request<unknown>(API.mcpStatus);
      return normalizeMcpStatus(payload);
    } catch (error) {
      if (!isNotImplemented(error)) throw error;
      return { enabled: "Not available", canva: "Not connected", tools: [] };
    }
  },
};
