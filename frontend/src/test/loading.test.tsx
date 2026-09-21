import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("loading states", () => {
  it("shows a loading state while the dashboard fetches", async () => {
    let resolveDashboard: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveDashboard = resolve;
    });
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/dashboard": () => pending,
    });

    renderApp(<App />, { route: "/dashboard" });
    expect(await screen.findByText(/loading dashboard/i)).toBeInTheDocument();
    resolveDashboard(
      jsonResponse({
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
      }),
    );
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });
});
