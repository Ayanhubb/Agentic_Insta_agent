import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

function jpegFile(): File {
  return new File(["jpeg-bytes"], "kundan.jpg", { type: "image/jpeg" });
}

describe("instagram upload publish", () => {
  it("does not post until an image and caption are confirmed", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/instagram/status": () =>
        jsonResponse({ connected: false, environment_configured: true, source: "environment" }),
    });
    renderApp(<App />, { route: "/instagram" });
    await screen.findByRole("heading", { name: "Instagram" });
    const post = screen.getByRole("button", { name: "Post to Instagram" });
    expect(post).toBeDisabled();
  });

  it("sends the image and caption after confirmation", async () => {
    const user = userEvent.setup({ delay: null });
    let posted = false;
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/instagram/status": () =>
        jsonResponse({ connected: false, environment_configured: true, source: "environment" }),
      "POST /api/v1/instagram/publish": (_url, init) => {
        posted = true;
        const body = init?.body as FormData;
        expect(body.get("caption")).toBe("Pal Jewels kundan necklace, made in Kolkata.");
        expect(body.get("image")).toBeInstanceOf(File);
        return jsonResponse(
          { success: true, task_id: "task-1", status: "pending", message: "Publishing started" },
          202,
        );
      },
      "GET /api/v1/tasks/task-1": () =>
        jsonResponse({
          success: true,
          task_id: "task-1",
          status: "completed",
          instagram_media_id: "media-1",
        }),
    });

    renderApp(<App />, { route: "/instagram" });
    await screen.findByRole("heading", { name: "New post" });
    const input = document.getElementById("instagram-image") as HTMLInputElement;
    await user.upload(input, jpegFile());
    await user.type(screen.getByLabelText("Caption"), "Pal Jewels kundan necklace, made in Kolkata.");
    await user.click(screen.getByRole("button", { name: "Post to Instagram" }));
    expect(posted).toBe(false);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(posted).toBe(false);
    await user.click(screen.getByRole("button", { name: "Post to Instagram" }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Post to Instagram" }));
    await waitFor(() => expect(posted).toBe(true));
  });
});
