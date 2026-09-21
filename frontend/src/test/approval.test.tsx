import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("approval", () => {
  it("requires explicit confirmation before approve and post", async () => {
    const user = userEvent.setup({ delay: null });
    let approved = false;
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "POST /api/v1/generation": () =>
        jsonResponse({
          image: {
            id: "job-1",
            original_prompt: "handloom saree",
            enhanced_prompt: "studio shot of a handloom saree",
            preview_url: "https://cdn.example.test/saree.jpg",
            approval_status: "PENDING",
          },
          published: false,
        }),
      "POST /api/v1/generation/job-1/approve": () => {
        approved = true;
        return jsonResponse({
          image: {
            id: "job-1",
            approval_status: "APPROVED",
            preview_url: "https://cdn.example.test/saree.jpg",
          },
          post: { id: "post-1", status: "PUBLISHING" },
        });
      },
      "POST /api/v1/generation/job-1/reject": () =>
        jsonResponse({ image: { id: "job-1", approval_status: "REJECTED" } }),
    });

    renderApp(<App />, { route: "/generate" });
    await screen.findByRole("heading", { name: "AI Generator" });
    await user.type(screen.getByLabelText("Prompt"), "handloom saree");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    await screen.findByRole("button", { name: "Approve & Post" });
    await user.click(screen.getByRole("button", { name: "Approve & Post" }));
    expect(approved).toBe(false);
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(approved).toBe(false);
    await user.click(screen.getByRole("button", { name: "Approve & Post" }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Approve & Post" }));
    await waitFor(() => expect(approved).toBe(true));
  });
});
