import type { HealthResponse } from "../../types/api";
import { request } from "./client";
import { API } from "./endpoints";

export const healthApi = {
  get(): Promise<HealthResponse> {
    return request<HealthResponse>(API.health);
  },
};
