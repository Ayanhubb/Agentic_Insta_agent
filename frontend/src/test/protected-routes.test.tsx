import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, forcedUser, jsonResponse, memberUser, mockApi, renderApp } from "./helpers";

describe("protected routes", () => {
  it("redirects anonymous users to login", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ error: { message: "Authentication required." } }, 401),
    });
    renderApp(<App />, { route: "/dashboard" });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("blocks non-admin users from /admin", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: memberUser }),
    });
    renderApp(<App />, { route: "/admin" });
    expect(await screen.findByText(/admin access is required/i)).toBeInTheDocument();
  });

  it("forces a password change before the dashboard", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: forcedUser }),
    });
    renderApp(<App />, { route: "/dashboard" });
    expect(await screen.findByRole("heading", { name: "Change your password" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("allows admins into /admin", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/health": () => jsonResponse({ status: "ok" }),
      "GET /api/v1/admin/users": () => jsonResponse({ users: [] }),
    });
    renderApp(<App />, { route: "/admin" });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Admin" })).toBeInTheDocument();
    });
  });
});
