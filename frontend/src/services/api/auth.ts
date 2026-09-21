import type { AuthResponse, User } from "../../types/api";
import { request, unwrapObject } from "./client";
import { API } from "./endpoints";
import { clearAccessToken, setAccessToken } from "./session";

function persistAuth(payload: AuthResponse): AuthResponse {
  const token = payload.access_token || payload.token;
  if (token) setAccessToken(token);
  return payload;
}

function asAuth(payload: unknown): AuthResponse {
  if (payload && typeof payload === "object" && "user" in payload) {
    return persistAuth(payload as AuthResponse);
  }
  const user = unwrapObject<User>(payload, ["user", "data"]);
  return persistAuth({ user });
}

export const authApi = {
  async register(email: string, password: string): Promise<AuthResponse> {
    const payload = await request<unknown>(API.auth.register, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return asAuth(payload);
  },

  async login(email: string, password: string): Promise<AuthResponse> {
    const payload = await request<unknown>(API.auth.login, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return asAuth(payload);
  },

  async logout(): Promise<void> {
    try {
      await request(API.auth.logout, { method: "POST" });
    } finally {
      clearAccessToken();
    }
  },

  async me(): Promise<User> {
    const payload = await request<unknown>(API.auth.me);
    if (payload && typeof payload === "object" && "user" in payload) {
      return (payload as AuthResponse).user;
    }
    return unwrapObject<User>(payload, ["user", "data"]);
  },

  async changePassword(currentPassword: string, newPassword: string): Promise<void> {
    await request(API.auth.changePassword, {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
  },
};
