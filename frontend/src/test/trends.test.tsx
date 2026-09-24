import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { TrendItem } from "../lib/trends";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const SECRET = "sk-deepseek-dashboard-secret";
const OPENAI = "sk-openai-dashboard-secret";
const META = "meta-token-dashboard-secret";
const CANVA = "canva-token-dashboard-secret";

function trend(overrides: Partial<TrendItem> = {}): TrendItem {
  return {
    id: "trend-1",
    title: "Silk House window",
    source: "Shop log",
    observed_at: "2026-09-20T10:00:00Z",
    freshness: "fresh",
    industry: "apparel",
    region: "Kolkata",
    evidence: [
      {
        kind: "observed",
        text: "Window traffic rose on Saturday.",
        source: "Shop log",
        observed_at: "2026-09-20T10:00:00Z",
      },
    ],
    confidence: "high",
    expires_at: "2026-12-01T00:00:00Z",
    expired: false,
    kind: "discovered",
    ...overrides,
  };
}

function authOk() {
  return jsonResponse({ user: adminUser });
}

function stubCompact(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

describe("trend intelligence dashboard", () => {
  beforeEach(() => {
    stubCompact(false);
  });

  it("shows a loading state", async () => {
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": () => new Promise(() => undefined),
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByText("Loading trends")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": () => jsonResponse({ trends: [] }),
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByText("No trends yet")).toBeInTheDocument();
  });

  it("shows an API failure", async () => {
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": () => jsonResponse({ error: { message: "boom" } }, 500),
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByRole("alert")).toHaveTextContent(/server ran into a problem/i);
  });

  it("keeps expired trends out of the current list", async () => {
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": () =>
        jsonResponse({
          trends: [
            trend(),
            trend({
              id: "trend-old",
              title: "Old monsoon palette",
              expires_at: "2020-01-01T00:00:00Z",
              freshness: "fresh",
              expired: false,
            }),
          ],
        }),
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByRole("heading", { name: "Current trends" })).toBeInTheDocument();
    const current = screen.getByRole("heading", { name: "Current trends" }).closest("section");
    const expired = screen.getByRole("heading", { name: "Expired trends" }).closest("section");
    expect(current).toHaveTextContent("Silk House window");
    expect(current).toHaveTextContent("Source: Shop log");
    expect(current).toHaveTextContent("Observed:");
    expect(current).not.toHaveTextContent("Old monsoon palette");
    expect(expired).toHaveTextContent("Old monsoon palette");
    expect(expired).toHaveTextContent("Expired");
    expect(screen.getAllByText("RESEARCH SIGNAL").length).toBeGreaterThan(0);
  });

  it("filters trends by region", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": (url) => {
        const region = url.searchParams.get("region");
        const trends = [
          trend(),
          trend({ id: "trend-2", title: "Mumbai snack board", region: "Mumbai", industry: "food" }),
        ].filter((item) => !region || item.region === region);
        return jsonResponse({ trends });
      },
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByText("Mumbai snack board")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Region"), "Kolkata");
    await waitFor(() => expect(screen.queryByText("Mumbai snack board")).not.toBeInTheDocument());
    expect(screen.getByText("Silk House window")).toBeInTheDocument();
    const researchCalls = fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => url.includes("/trends/research"));
    expect(researchCalls.some((url) => url.includes("region=Kolkata"))).toBe(true);
  });

  it("filters trends by industry", async () => {
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": (url) => {
        const industry = url.searchParams.get("industry");
        const trends = [
          trend(),
          trend({ id: "trend-2", title: "Mumbai snack board", region: "Mumbai", industry: "food" }),
        ].filter((item) => !industry || item.industry === industry);
        return jsonResponse({ trends });
      },
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByText("Mumbai snack board")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Industry"), "food");
    await waitFor(() => expect(screen.queryByText("Silk House window")).not.toBeInTheDocument());
    expect(screen.getByText("Mumbai snack board")).toBeInTheDocument();
  });

  it("stacks the dashboard on a mobile layout", async () => {
    stubCompact(true);
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": () => jsonResponse({ trends: [] }),
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByRole("heading", { name: "Trend Intelligence" })).toBeInTheDocument();
    expect(document.querySelector(".trends-page")).toHaveAttribute("data-layout", "stack");
  });

  it("requires authentication", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ error: { message: "Authentication required." } }, 401),
    });
    renderApp(<App />, { route: "/trends" });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Trend Intelligence" })).not.toBeInTheDocument();
  });

  it("keeps another tenant's trends and provider secrets off the page", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/research": () =>
        jsonResponse({
          trends: [
            {
              ...trend(),
              deepseek_api_key: SECRET,
              openai_api_key: OPENAI,
              meta_access_token: META,
              canva_token: CANVA,
            },
          ],
        }),
    });
    renderApp(<App />, { route: "/trends/research" });
    expect(await screen.findByText("Silk House window")).toBeInTheDocument();
    expect(screen.queryByText("Other Tenant Secret Trend")).not.toBeInTheDocument();
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
    expect(screen.queryByText(OPENAI)).not.toBeInTheDocument();
    expect(screen.queryByText(META)).not.toBeInTheDocument();
    expect(screen.queryByText(CANVA)).not.toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes("user-2") || url.includes("user_id"))).toBe(false);
    expect(screen.getByText("RESEARCH SIGNAL")).toBeInTheDocument();
    expect(screen.getByText("OBSERVED DATA")).toBeInTheDocument();
  });

  it("labels a content recommendation separately from observed facts", async () => {
    mockApi({
      "GET /api/v1/auth/me": authOk,
      "GET /api/v1/trends/opportunities": () =>
        jsonResponse({
          opportunities: [
            {
              id: "opp-1",
              title: "Diwali window",
              why_now: "The festival week is starting.",
              evidence: [{ kind: "discovered", text: "Search interest rose.", source: "Notes" }],
              product: "Silk saree",
              festival: "Diwali",
              recommended_format: "Reel",
              creative_direction: "Warm light on silk.",
              expires_at: "2026-12-01T00:00:00Z",
              expired: false,
              confidence: "medium",
              industry: "apparel",
              region: "Kolkata",
              status: "open",
              kind: "recommended",
              disclaimer: "This is a content recommendation, not an observed fact.",
            },
          ],
        }),
      "GET /api/v1/trends/opportunities/opp-1/evidence": () =>
        jsonResponse({
          opportunity_id: "opp-1",
          evidence: [{ kind: "discovered", text: "Search interest rose.", source: "Notes" }],
          note: "Evidence is what was recorded. The content recommendation is separate.",
        }),
    });
    renderApp(<App />, { route: "/trends/opportunities" });
    expect(await screen.findByText("This is a content recommendation, not an observed fact.")).toBeInTheDocument();
    expect(screen.getByText("CONTENT RECOMMENDATION")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Content" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "View Evidence" }));
    expect(await screen.findByRole("region", { name: "Evidence" })).toHaveTextContent("RESEARCH SIGNAL");
  });
});
