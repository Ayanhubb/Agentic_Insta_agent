import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, emptyDashboard, jsonResponse, mockApi, renderApp } from "./helpers";

describe("login", () => {
  it("signs in with email and password", async () => {
    const user = userEvent.setup({ delay: null });
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ error: { message: "Authentication required." } }, 401),
      "POST /api/v1/auth/login": async (_url, init) => {
        const body = JSON.parse(String(init?.body));
        expect(body.email).toBe("admin@example.com");
        expect(body.password).toBe("secret-pass");
        return jsonResponse({
          user: adminUser,
          access_token: "token-1",
        });
      },
      "GET /api/v1/dashboard": () => jsonResponse(emptyDashboard),
    });

    renderApp(<App />, { route: "/login" });
    await screen.findByRole("heading", { name: "Sign in" });

    await user.type(screen.getByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "secret-pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
    expect(window.localStorage.getItem("agentic.access_token")).toBe("token-1");
  });

  it("shows an error for invalid credentials", async () => {
    const user = userEvent.setup({ delay: null });
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ error: { message: "Authentication required." } }, 401),
      "POST /api/v1/auth/login": () =>
        jsonResponse({ error: { code: "AUTHENTICATION_ERROR", message: "Invalid email or password." } }, 401),
    });

    renderApp(<App />, { route: "/login" });
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText(/invalid email or password/i)).toBeInTheDocument();
  });

  it("does not hard-code a default password in the login page", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ error: { message: "Authentication required." } }, 401),
    });
    const { container } = renderApp(<App />, { route: "/login" });
    await screen.findByRole("heading", { name: "Sign in" });
    expect(container.textContent).not.toContain("12345");
    expect(container.innerHTML).not.toContain("DEFAULT_ADMIN_PASSWORD");
  });
});
