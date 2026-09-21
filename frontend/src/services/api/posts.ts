import type { InstagramPost } from "../../types/api";
import { request, unwrapList } from "./client";
import { API } from "./endpoints";

export const postsApi = {
  async list(): Promise<InstagramPost[]> {
    const payload = await request<unknown>(API.posts);
    return unwrapList<InstagramPost>(payload, ["items", "data", "results", "posts"]);
  },
};
