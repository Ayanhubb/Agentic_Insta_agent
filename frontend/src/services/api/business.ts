import type { BusinessProfile } from "../../types/api";
import { request, unwrapObject } from "./client";
import { API } from "./endpoints";

export const businessApi = {
  async get(): Promise<BusinessProfile | null> {
    const payload = await request<unknown>(API.business);
    if (payload && typeof payload === "object" && "profile" in payload) {
      const profile = (payload as { profile: BusinessProfile | null }).profile;
      return profile;
    }
    return unwrapObject<BusinessProfile>(payload, ["data"]);
  },

  async update(profile: BusinessProfile): Promise<BusinessProfile> {
    const payload = await request<unknown>(API.business, {
      method: "PUT",
      body: JSON.stringify(profile),
    });
    return unwrapObject<BusinessProfile>(payload, ["profile", "data"]);
  },
};
