import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { normalizeMcpStatus, normalizeProviderStatus } from "../lib/providerStatus";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const SECRET = "sk-live-should-not-render";
const META = "EAAB-should-not-render";
const JWT = "jwt-should-not-render";
const ENCRYPTION = "fernet-should-not-render";

describe("provider status", () => {
  it("maps connection flags and drops secret fields", () => {
    const status = normalizeProviderStatus({
      deepseek_configured: true,
      openai_configured: false,
      canva_configured: true,
      llm_provider: "deepseek",
      openai_api_key: SECRET,
      meta_access_token: META,
      jwt_secret: JWT,
      encryption_key: ENCRYPTION,
    });
    expect(status.deepseek).toBe("Connected");
    expect(status.openai).toBe("Not configured");
    expect(status.canva).toBe("Connected");
    expect(status.llmProvider).toBe("deepseek");
    expect(JSON.stringify(status)).not.toContain(SECRET);
    expect(JSON.stringify(status)).not.toContain(META);
    expect(JSON.stringify(status)).not.toContain(JWT);
    expect(JSON.stringify(status)).not.toContain(ENCRYPTION);
  });

  it("keeps MCP tool names and ignores tokens", () => {
    const status = normalizeMcpStatus({
      enabled: true,
      canva_mcp_token: META,
      tools: [
        { name: "brand.guidelines", available: true, access_token: SECRET },
        { name: "https://evil.example", available: true },
      ],
    });
    expect(status.enabled).toBe("On");
    expect(status.tools.map((tool) => tool.name)).toEqual(["brand.guidelines"]);
    expect(JSON.stringify(status)).not.toContain(SECRET);
    expect(JSON.stringify(status)).not.toContain(META);
  });
});

describe("catalog pages", () => {
  it("saves brand guidelines and uploads a logo without showing secrets", async () => {
    const user = userEvent.setup({ delay: null });
    const calls: string[] = [];
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/brand": () =>
        jsonResponse({
          brand: {
            company_name: "Yotto",
            brand_colors: [{ hex: "#0e6b63" }],
            guidelines: [{ title: "Brand guidelines", body: "Quiet luxury" }],
            festival_preferences: [],
          },
        }),
      "GET /api/v1/festivals": () => jsonResponse({ festivals: [{ festival_name: "Diwali", date: "2026-10-17" }] }),
      "POST /api/v1/brand": async (_url, init) => {
        calls.push("brand");
        const body = JSON.parse(String(init?.body));
        const saves = calls.filter((item) => item === "brand").length;
        if (saves === 1) {
          expect(body.company_name).toBe("Yotto");
          expect(body.guidelines).toContain("heritage");
          expect(body.brand_colors).toEqual([{ hex: "#112233" }]);
          expect(body.festival_preferences).toContain("Diwali");
        } else {
          expect(body.logo_png_asset_id).toBe("logo-1");
        }
        expect(JSON.stringify(body)).not.toMatch(/api_key|access_token|jwt_secret/i);
        return jsonResponse({ brand: { ...body, logo_png: body.logo_png_asset_id ? { id: body.logo_png_asset_id, media_url: "/api/v1/assets/logo-1/media" } : null } });
      },
      "POST /api/v1/assets": async (_url, init) => {
        calls.push("logo");
        expect(init?.body).toBeInstanceOf(FormData);
        const form = init?.body as FormData;
        expect(form.get("role")).toBe("logo_png");
        expect(form.get("file")).toBeTruthy();
        return jsonResponse({ asset: { id: "logo-1", role: "logo_png", media_url: "/api/v1/assets/logo-1/media" } });
      },
    });

    renderApp(<App />, { route: "/brand" });
    const guidelines = await screen.findByLabelText("Brand guidelines");
    await user.clear(guidelines);
    await user.type(guidelines, "heritage gold");
    await user.clear(screen.getByLabelText(/brand colors/i));
    await user.type(screen.getByLabelText(/brand colors/i), "#112233");
    await user.click(screen.getByRole("checkbox", { name: "Diwali" }));
    await user.click(screen.getByRole("button", { name: "Save brand" }));
    const file = new File(["logo"], "logo.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Company logo"), file);
    await user.click(screen.getByRole("button", { name: "Upload logo" }));
    await waitFor(() => expect(calls).toEqual(["brand", "logo", "brand"]));
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("creates a product with metadata and an image", async () => {
    const user = userEvent.setup({ delay: null });
    const calls: string[] = [];
    const products: { id: string; name: string; category?: string }[] = [];
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/products": () => jsonResponse({ products }),
      "POST /api/v1/products": async (_url, init) => {
        calls.push("product");
        const body = JSON.parse(String(init?.body));
        expect(body.name).toBe("Kundan necklace");
        expect(body.category).toBe("Jewellery");
        expect(body.price).toBe("4999");
        expect(body.offer).toBe("Festive price");
        const product = { id: "prod-1", name: body.name, category: body.category, offer: body.offer, price: body.price };
        products.push(product);
        return jsonResponse({ product });
      },
      "POST /api/v1/assets": async (_url, init) => {
        calls.push("image");
        const form = init?.body as FormData;
        expect(form.get("role")).toBe("product_image");
        expect(form.get("product_id")).toBe("prod-1");
        return jsonResponse({ asset: { id: "img-1", role: "product_image", media_url: "/api/v1/assets/img-1/media" } });
      },
    });

    renderApp(<App />, { route: "/products" });
    await user.type(await screen.findByLabelText("Product name"), "Kundan necklace");
    await user.type(screen.getByLabelText("Product category"), "Jewellery");
    await user.type(screen.getByLabelText("Price"), "₹4999");
    await user.type(screen.getByLabelText("Offer"), "Festive price");
    const file = new File(["img"], "necklace.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("Product image"), file);
    await user.click(screen.getByRole("button", { name: "Save product" }));
    await waitFor(() => expect(calls).toEqual(["product", "image"]));
    expect((await screen.findAllByText("Kundan necklace")).length).toBeGreaterThan(0);
  });

  it("shows AI connection labels and hides secrets", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/ai/status": () =>
        jsonResponse({
          deepseek_configured: true,
          openai_configured: false,
          canva_connected: false,
          openai_api_key: SECRET,
          meta_access_token: META,
          jwt_secret: JWT,
          encryption_key: ENCRYPTION,
        }),
    });
    renderApp(<App />, { route: "/ai-settings" });
    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek").parentElement).toHaveTextContent("Connected");
    expect(screen.getByText("OpenAI").parentElement).toHaveTextContent("Not configured");
    expect(screen.getByText("Canva").parentElement).toHaveTextContent("Not connected");
    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.textContent).not.toContain(META);
    expect(document.body.textContent).not.toContain(JWT);
    expect(document.body.textContent).not.toContain(ENCRYPTION);
  });

  it("shows MCP tool names and not tokens", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/mcp/status": () =>
        jsonResponse({
          enabled: true,
          canva_enabled: false,
          canva_mcp_token: META,
          tools: [{ name: "product.get", available: true }],
        }),
    });
    renderApp(<App />, { route: "/mcp" });
    expect(await screen.findByRole("heading", { name: "MCP" })).toBeInTheDocument();
    expect(screen.getByText("product.get")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(META);
  });

  it("lists campaigns and links generate without calling Meta", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/festivals/campaigns": () =>
        jsonResponse({
          campaigns: [
            {
              id: "camp-1",
              festival_name: "Diwali",
              festival_date: "2026-10-17",
              required_posts: 2,
              published_posts: 0,
              remaining_posts: 2,
              status: "active",
            },
          ],
        }),
    });
    renderApp(<App />, { route: "/campaigns" });
    expect(await screen.findByRole("heading", { name: "Campaigns" })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Generate" });
    expect(link).toHaveAttribute("href", "/generate?festival=Diwali");
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.join(" ")).not.toMatch(/graph\.facebook\.com|graph\.instagram\.com/);
  });
});

describe("generation context", () => {
  it("shows festival, business, product, brief, and QA status", async () => {
    const user = userEvent.setup({ delay: null });
    const fetchMock = mockApi({
      "GET /api/v1/auth/me": () => jsonResponse({ user: adminUser }),
      "GET /api/v1/business": () => jsonResponse({ profile: { business_name: "Yotto Atelier" } }),
      "GET /api/v1/products": () => jsonResponse({ products: [{ id: "prod-1", name: "Kundan necklace" }] }),
      "GET /api/v1/festivals": () => jsonResponse({ festivals: [{ festival_name: "Diwali" }] }),
      "POST /api/v1/generation": async (_url, init) => {
        const body = JSON.parse(String(init?.body));
        expect(body.prompt).toBe("window display");
        expect(body.festival).toBe("Diwali");
        expect(body.product_id).toBe("prod-1");
        return jsonResponse({
          image: {
            id: "img-9",
            original_prompt: body.prompt,
            creative_brief: "Gold jhumkas in warm light",
            generation_status: "GENERATED",
            qa_status: "PASSED",
            approval_status: "PENDING",
            festival: "Diwali",
            business_name: "Yotto Atelier",
            product_name: "Kundan necklace",
          },
          published: false,
        });
      },
      "POST /api/v1/generation/img-9/regenerate": () =>
        jsonResponse({
          image: {
            id: "img-9",
            creative_brief: "Regenerated brief",
            generation_status: "GENERATED",
            qa_status: "PASSED",
            approval_status: "PENDING",
          },
        }),
      "POST /api/v1/generation/img-9/reject": () =>
        jsonResponse({ image: { id: "img-9", approval_status: "REJECTED" } }),
    });

    renderApp(<App />, { route: "/generate?festival=Diwali" });
    await screen.findByRole("heading", { name: "AI Generator" });
    expect(await screen.findByText("Yotto Atelier")).toBeInTheDocument();
    await screen.findByRole("option", { name: "Kundan necklace" });
    await user.selectOptions(screen.getByLabelText("Product"), "prod-1");
    await user.type(screen.getByLabelText("Prompt"), "window display");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByText("Gold jhumkas in warm light")).toBeInTheDocument();
    expect(screen.getByText("Image generation status").parentElement).toHaveTextContent("GENERATED");
    expect(screen.getByText("Image QA status").parentElement).toHaveTextContent("PASSED");
    expect(screen.getByText("Approval status").parentElement).toHaveTextContent("PENDING");
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(await screen.findByText("Regenerated brief")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.join(" ")).not.toMatch(/graph\.facebook\.com|graph\.instagram\.com/);
  });
});
