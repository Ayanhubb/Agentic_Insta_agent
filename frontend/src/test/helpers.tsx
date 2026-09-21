import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "../context/AuthContext";
import { ToastProvider } from "../context/ToastContext";
import type { User } from "../types/api";

export const adminUser: User = {
  id: "user-1",
  email: "admin@example.com",
  is_admin: true,
  must_change_password: false,
};

export const memberUser: User = {
  id: "user-2",
  email: "member@example.com",
  is_admin: false,
  must_change_password: false,
};

export const forcedUser: User = {
  ...adminUser,
  must_change_password: true,
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export const emptyDashboard = {
  total_posts: 0,
  todays_posts: 0,
  monthly_posts: 0,
  generated_images: 0,
  published_posts: 0,
  festival_posts: 0,
  daily_automation: false,
  festival_automation: false,
  instagram_connected: false,
  recent_images: [],
  recent_posts: [],
  upcoming_festival: null,
  next_scheduled_post: null,
  recent_activity: [],
};

type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;

export function mockApi(routes: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${url.pathname}`;
    const handler = routes[key];
    if (!handler) {
      return jsonResponse({ error: { code: "NOT_FOUND", message: "Not found." } }, 404);
    }
    return handler(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function renderApp(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ToastProvider>
        <AuthProvider>{ui}</AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}
