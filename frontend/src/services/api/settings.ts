import type { AutomationSettings, User } from "../../types/api";
import { request } from "./client";
import { API } from "./endpoints";

export interface SettingsPayload {
  user?: User;
  automation?: AutomationSettings;
}

export const settingsApi = {
  get(): Promise<SettingsPayload> {
    return request<SettingsPayload>(API.settings);
  },
};
