import type { AutomationSettings } from "../../types/api";
import { request, unwrapObject } from "./client";
import { API } from "./endpoints";

export const automationApi = {
  async get(): Promise<AutomationSettings> {
    const payload = await request<unknown>(API.automation.get);
    return unwrapObject<AutomationSettings>(payload, ["data", "settings"]);
  },

  async update(settings: AutomationSettings): Promise<AutomationSettings> {
    const payload = await request<unknown>(API.automation.put, {
      method: "PUT",
      body: JSON.stringify(settings),
    });
    return unwrapObject<AutomationSettings>(payload, ["data", "settings"]);
  },

  async runNow(): Promise<unknown> {
    return request(API.automation.runNow, { method: "POST" });
  },
};
