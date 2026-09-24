import type { InstagramStatus, PublishResponse } from "../../types/api";
import { request, unwrapObject } from "./client";
import { API } from "./endpoints";

export const instagramApi = {
  async status(): Promise<InstagramStatus> {
    const payload = await request<unknown>(API.instagram.status);
    return unwrapObject<InstagramStatus>(payload, ["data", "account", "status"]);
  },

  async connect(body: { instagram_account_id: string; access_token: string }): Promise<InstagramStatus> {
    return request<InstagramStatus>(API.instagram.connect, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async disconnect(): Promise<void> {
    await request(API.instagram.disconnect, { method: "POST" });
  },

  async publish(image: File, wait = false, caption?: string): Promise<PublishResponse> {
    const form = new FormData();
    form.append("image", image);
    const text = caption?.trim();
    if (text) form.append("caption", text);
    const query = wait ? "?wait=true" : "";
    return request<PublishResponse>(`${API.instagram.publish}${query}`, {
      method: "POST",
      body: form,
    });
  },
};
