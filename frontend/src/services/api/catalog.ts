import type { BrandAsset, BrandProfile, CatalogProduct } from "../../types/api";
import { request, unwrapList, unwrapObject } from "./client";
import { API } from "./endpoints";

function asBrand(payload: unknown): BrandProfile | null {
  if (payload && typeof payload === "object" && "brand" in payload) {
    const brand = (payload as { brand: BrandProfile | null }).brand;
    return brand;
  }
  if (!payload || typeof payload !== "object") return null;
  return unwrapObject<BrandProfile>(payload, ["data"]);
}

function asAsset(payload: unknown): BrandAsset {
  return unwrapObject<BrandAsset>(payload, ["asset", "data"]);
}

function asProduct(payload: unknown): CatalogProduct {
  return unwrapObject<CatalogProduct>(payload, ["product", "data"]);
}

export interface ProductInput {
  name: string;
  description?: string;
  category?: string;
  price?: string;
  sku?: string;
  is_active?: boolean;
  offer?: string;
  image_asset_id?: string;
}

export const brandApi = {
  async get(): Promise<BrandProfile | null> {
    const payload = await request<unknown>(API.brand);
    return asBrand(payload);
  },

  async save(body: Record<string, unknown>): Promise<BrandProfile> {
    const payload = await request<unknown>(API.brand, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return asBrand(payload) ?? {};
  },

  async assets(): Promise<BrandAsset[]> {
    const payload = await request<unknown>(API.assets.list);
    return unwrapList<BrandAsset>(payload, ["assets", "items", "data", "results"]);
  },

  async uploadAsset(file: File, role: string, productId?: string): Promise<BrandAsset> {
    const form = new FormData();
    form.append("file", file);
    form.append("role", role);
    if (productId) form.append("product_id", productId);
    const payload = await request<unknown>(API.assets.list, { method: "POST", body: form });
    return asAsset(payload);
  },

  async deleteAsset(id: string): Promise<void> {
    await request(API.assets.one(id), { method: "DELETE" });
  },
};

export const productsApi = {
  async list(): Promise<CatalogProduct[]> {
    const payload = await request<unknown>(API.products.list);
    return unwrapList<CatalogProduct>(payload, ["products", "items", "data", "results"]);
  },

  async create(body: ProductInput): Promise<CatalogProduct> {
    const payload = await request<unknown>(API.products.list, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return asProduct(payload);
  },

  async update(id: string, body: Partial<ProductInput>): Promise<CatalogProduct> {
    const payload = await request<unknown>(API.products.one(id), {
      method: "PUT",
      body: JSON.stringify(body),
    });
    return asProduct(payload);
  },

  async uploadImage(id: string, file: File): Promise<BrandAsset> {
    return brandApi.uploadAsset(file, "product_image", id);
  },
};

export function assetMediaUrl(asset: BrandAsset | null | undefined): string | null {
  if (!asset) return null;
  if (asset.media_url) return asset.media_url;
  if (asset.id) return API.assets.media(asset.id);
  return null;
}
