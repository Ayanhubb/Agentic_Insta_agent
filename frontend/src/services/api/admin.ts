import type { User } from "../../types/api";
import { request, unwrapList } from "./client";
import { API } from "./endpoints";

export const adminApi = {
  async users(): Promise<User[]> {
    const payload = await request<unknown>(API.adminUsers);
    return unwrapList<User>(payload, ["users", "items", "data"]);
  },
};
