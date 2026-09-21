import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("dashboard", () => {
  it("renders post, image, automation, and Instagram stats", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/dashboard": () =>
        jsonResponse({
          total_posts: 2,
          todays_posts: 1,
          monthly_posts: 1,
          generated_images: 1,
          published_posts: 1,
          festival_posts: 1,
          daily_automation: true,
          festival_automation: false,
          instagram_connected: true,
          recent_images: [{ id: "img-1", original_prompt: "diyas", approval_status: "APPROVED" }],
          recent_posts: [
            { id: "p1", status: "PUBLISHED", source: "FESTIVAL_AUTOMATION", published_at: new Date().toISOString() },
            { id: "p2", status: "FAILED", source: "USER_PROMPT" },
          ],
          upcoming_festival: { festival_name: "Diwali", date: "2026-10-17" },
          next_scheduled_post: "10:00",
          recent_activity: [{ label: "Post PUBLISHED", at: new Date().toISOString() }],
        }),
    });

    renderApp(<App />, { route: "/dashboard" });
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText("Total posts").closest(".card")).toHaveTextContent("2");
    expect(screen.getByText("Published posts").closest(".card")).toHaveTextContent("1");
    expect(screen.getByText("Generated images").closest(".card")).toHaveTextContent("1");
    expect(screen.getByText("Daily automation").closest(".card")).toHaveTextContent("On");
    expect(screen.getByText("Festival automation").closest(".card")).toHaveTextContent("Off");
    expect(screen.getByText("Instagram connection").closest(".card")).toHaveTextContent("Connected");
    expect(screen.getByText("Diwali")).toBeInTheDocument();
    expect(screen.getByText("10:00")).toBeInTheDocument();
    expect(screen.getByText("Post PUBLISHED")).toBeInTheDocument();
    expect(screen.getByText("diyas")).toBeInTheDocument();
  });
});
