import type { Festival, FestivalCampaign, FestivalSettings } from "../../types/api";
import { request, unwrapList, unwrapObject } from "./client";
import { API } from "./endpoints";

export const festivalsApi = {
  async list(): Promise<Festival[]> {
    const payload = await request<unknown>(API.festivals.list);
    return unwrapList<Festival>(payload, ["items", "data", "results", "festivals"]);
  },

  async campaigns(): Promise<FestivalCampaign[]> {
    const payload = await request<unknown>(API.festivals.campaigns);
    return unwrapList<FestivalCampaign>(payload, [
      "items",
      "data",
      "results",
      "campaigns",
    ]);
  },

  async updateSettings(settings: FestivalSettings): Promise<FestivalSettings> {
    const payload = await request<unknown>(API.festivals.settings, {
      method: "PUT",
      body: JSON.stringify(settings),
    });
    return unwrapObject<FestivalSettings>(payload, ["data", "settings"]);
  },
};
