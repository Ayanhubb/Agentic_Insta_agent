import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("automation", () => {
  it("warns before enabling automatic publication", async () => {
    const user = userEvent.setup({ delay: null });
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/automation": () =>
        jsonResponse({
          daily_enabled: true,
          daily_posts_per_day: 1,
          daily_post_time: "10:00",
          auto_daily_publish: false,
          festival_enabled: false,
          festival_posts_per_festival: 2,
        }),
    });

    renderApp(<App />, { route: "/automation" });
    await screen.findByRole("heading", { name: "Automation" });
    await user.click(screen.getByLabelText("Auto publish"));
    expect(await screen.findByText("Enable automatic publication?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByLabelText("Auto publish")).not.toBeChecked();
  });
});
