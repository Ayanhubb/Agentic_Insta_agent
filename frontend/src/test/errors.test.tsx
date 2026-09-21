import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("user errors", () => {
  it("surfaces 422 validation errors on login", async () => {
    const user = userEvent.setup({ delay: null });
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ error: { message: "Authentication required." } }, 401),
      "POST /api/v1/auth/login": () =>
        jsonResponse({ detail: [{ loc: ["body", "email"], msg: "value is not a valid email address" }] }, 422),
    });
    renderApp(<App />, { route: "/login" });
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "secret-pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
  });

  it("surfaces 403 errors from protected APIs", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/generation": () =>
        jsonResponse({ error: { code: "PERMISSION_ERROR", message: "Forbidden." } }, 403),
    });
    renderApp(<App />, { route: "/images" });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/permission/i);
    });
  });

  it("surfaces 500 errors from the dashboard", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/dashboard": () => jsonResponse({ error: { message: "boom" } }, 500),
    });
    renderApp(<App />, { route: "/dashboard" });
    expect(await screen.findByText(/server ran into a problem/i)).toBeInTheDocument();
  });
});
