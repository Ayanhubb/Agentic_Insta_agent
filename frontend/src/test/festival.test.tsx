import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("festival", () => {
  it("shows required, published, remaining, and progress", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/festivals/campaigns": () =>
        jsonResponse({
          campaigns: [
            {
              id: "c1",
              festival_name: "Holi",
              festival_date: "2026-03-14",
              required_posts: 2,
              generated_posts: 2,
              published_posts: 1,
              remaining_posts: 1,
              status: "active",
            },
          ],
        }),
    });

    renderApp(<App />, { route: "/festivals" });
    expect(await screen.findByText("Holi")).toBeInTheDocument();
    expect(screen.getByText("Generated").closest("table")).toHaveTextContent("2");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });
});
