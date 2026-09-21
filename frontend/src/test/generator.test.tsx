import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("generator", () => {
  it("creates a content job from a prompt and does not auto-publish", async () => {
    const user = userEvent.setup({ delay: null });
    let created = false;
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "POST /api/v1/generation": async (_url, init) => {
        created = true;
        const body = JSON.parse(String(init?.body));
        expect(body.prompt).toContain("diwali collection");
        return jsonResponse({
          image: {
            id: "job-1",
            original_prompt: body.prompt,
            enhanced_prompt: "refined festive boutique display",
            preview_url: "https://cdn.example.test/preview.jpg",
            approval_status: "PENDING",
          },
          published: false,
        });
      },
    });

    renderApp(<App />, { route: "/generate" });
    await screen.findByRole("heading", { name: "AI Generator" });
    await user.type(screen.getByLabelText("Prompt"), "diwali collection window display");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => {
      expect(screen.getByText("refined festive boutique display")).toBeInTheDocument();
    });
    expect(created).toBe(true);
    expect(screen.queryByText(/publishing started/i)).not.toBeInTheDocument();
  });

  it("shows a backend-unavailable error when generation is not implemented", async () => {
    const user = userEvent.setup({ delay: null });
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
    });
    renderApp(<App />, { route: "/generate" });
    await screen.findByRole("heading", { name: "AI Generator" });
    await user.type(screen.getByLabelText("Prompt"), "a product photo");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByText(/content generation API is not available/i)).toBeInTheDocument();
  });
});
