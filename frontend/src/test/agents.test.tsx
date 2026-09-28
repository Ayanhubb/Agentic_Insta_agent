import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { receivePublic } from "../lib/agentConfig";
import type { PlatformAgent } from "../types/agents";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const SECRET = "sk-live-should-not-render";

function version(overrides: Record<string, unknown> = {}) {
  return {
    version_number: 1,
    status: "draft",
    workflow_key: "instagram.reference",
    trigger_key: "reference.run",
    approval_policy_id: "instagram.still_image",
    tool_names: ["get_business_profile"],
    knowledge_sources: ["business"],
    asset_roles: ["company_logo"],
    models: [
      { role: "content_planning", implementation: "deepseek", model: "deepseek-flash" },
      { role: "image_generation", implementation: "openai", model: "gpt-image-1" },
      { role: "vision_qa", implementation: "deepseek", model: "" },
    ],
    timeout_seconds: 120,
    ...overrides,
  };
}

const systemAgent: PlatformAgent = {
  key: "instagram_marketing",
  display_name: "Instagram Marketing Agent",
  description: "Read-only reference agent.",
  owner: "system",
  status: "active",
  published_version_number: 1,
  draft_version_number: null,
  writable: false,
  versions: [version({ status: "published", tool_names: ["get_business_profile", "publish_instagram_media"] })],
};

function userAgent(name = "Window agent"): PlatformAgent {
  return {
    ...systemAgent,
    key: "window_agent",
    display_name: name,
    description: "Shop window",
    owner: "user",
    writable: true,
    published_version_number: null,
    draft_version_number: 1,
    versions: [version()],
  };
}

function authRoutes(extra: Record<string, (url: URL, init?: RequestInit) => Response | Promise<Response>> = {}) {
  return mockApi({
    "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
    "GET /api/v1/agents": () => jsonResponse({ agents: [systemAgent] }),
    "GET /api/v1/agents/instagram_marketing": () => jsonResponse({ agent: systemAgent }),
    "GET /api/v1/agents/window_agent": () => jsonResponse({ agent: userAgent() }),
    "GET /api/v1/agents/window_agent/executions": () => jsonResponse({ executions: [] }),
    "GET /api/v1/agents/instagram_marketing/executions": () => jsonResponse({ executions: [] }),
    ...extra,
  });
}

describe("agent builder", () => {
  it("drops credentials and publishing tool grants before the page can render them", () => {
    expect(() =>
      receivePublic({
        agent: { display_name: "Window", api_key: SECRET, versions: [] },
      }),
    ).toThrow(/credential/i);
    const cleaned = receivePublic<{ agent: PlatformAgent }>({
      agent: userAgent(),
    });
    const granted = receivePublic<PlatformAgent>({
      ...userAgent(),
      versions: [version({ tool_names: ["get_business_profile", "publish_instagram_media"] })],
    });
    expect(cleaned.agent.display_name).toBe("Window agent");
    expect(granted.versions[0].tool_names).toEqual(["get_business_profile"]);
    expect(JSON.stringify(granted)).not.toContain("publish_instagram_media");
  });

  it("lists agents and creates one without a secret field", async () => {
    const user = userEvent.setup({ delay: null });
    let posted: unknown = null;
    authRoutes({
      "POST /api/v1/agents": (_url, init) => {
        posted = JSON.parse(String(init?.body));
        return jsonResponse({ agent: userAgent("Window agent") });
      },
    });
    renderApp(<App />, { route: "/agents" });
    expect(await screen.findByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByText("Instagram Marketing Agent")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "New agent" }));
    expect(await screen.findByRole("heading", { name: "New agent" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Agent name"), "Window agent");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByRole("heading", { name: "Window agent" })).toBeInTheDocument();
    expect(posted).toEqual({ key: "window_agent", display_name: "Window agent", description: "" });
    expect(JSON.stringify(posted)).not.toContain(SECRET);
  });

  it("refuses to show a credential returned with an agent", async () => {
    authRoutes({
      "GET /api/v1/agents/window_agent": () =>
        jsonResponse({ agent: { ...userAgent(), openai_api_key: SECRET } }),
    });
    renderApp(<App />, { route: "/agents/window_agent" });
    expect(await screen.findByRole("alert")).toHaveTextContent(/credential/i);
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("configures models without an API key field", async () => {
    const user = userEvent.setup({ delay: null });
    let saved: { models?: { role: string; implementation: string; model: string }[] } | null = null;
    authRoutes({
      "PATCH /api/v1/agents/window_agent": (_url, init) => {
        saved = JSON.parse(String(init?.body));
        return jsonResponse({ agent: userAgent() });
      },
    });
    renderApp(<App />, { route: "/agents/window_agent/models" });
    expect(await screen.findByRole("heading", { name: "Window agent" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("LLM provider"), "mock");
    const model = screen.getByRole("textbox", { name: /LLM model/i });
    await user.clear(model);
    await user.type(model, "mock-model");
    await user.click(screen.getByRole("button", { name: "Save models" }));
    await waitFor(() => {
      expect(saved?.models?.find((item) => item.role === "content_planning")).toMatchObject({
        implementation: "mock",
        model: "mock-model",
      });
    });
    expect(JSON.stringify(saved)).not.toContain("api_key");
  });

  it("does not offer Instagram publishing tools", async () => {
    authRoutes({
      "GET /api/v1/agents/window_agent": () =>
        jsonResponse({
          agent: {
            ...userAgent(),
            versions: [version({ tool_names: ["get_business_profile", "publish_instagram_media"] })],
          },
        }),
    });
    renderApp(<App />, { route: "/agents/window_agent/tools" });
    expect(await screen.findByText("get business profile")).toBeInTheDocument();
    expect(screen.queryByText("publish instagram media")).not.toBeInTheDocument();
    expect(screen.queryByText("publish_instagram_media")).not.toBeInTheDocument();
  });

  it("shows the reference workflow as read-only", async () => {
    authRoutes();
    renderApp(<App />, { route: "/agents/instagram_marketing/workflow" });
    expect(await screen.findByRole("heading", { name: "Instagram Marketing Agent" })).toBeInTheDocument();
    expect(screen.getByText(/load business context/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save workflow/i })).not.toBeInTheDocument();
  });

  it("shows an empty execution list", async () => {
    authRoutes();
    renderApp(<App />, { route: "/agents/instagram_marketing/executions" });
    expect(await screen.findByRole("heading", { name: "Instagram Marketing Agent" })).toBeInTheDocument();
    expect(await screen.findByText("No executions")).toBeInTheDocument();
  });

  it("keeps the Instagram studio page available", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/instagram/status": () =>
        jsonResponse({
          connected: false,
          environment_configured: false,
          source: null,
          instagram_account_id: null,
        }),
    });
    renderApp(<App />, { route: "/instagram" });
    expect(await screen.findByRole("heading", { name: "Instagram" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Post to Instagram" })).toBeInTheDocument();
  });
});
