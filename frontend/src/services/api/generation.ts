import type { ContentJob, GeneratedImage, GenerationCreateOptions } from "../../types/api";
import { request, unwrapList, unwrapObject } from "./client";
import { API } from "./endpoints";

function asImage(payload: unknown): GeneratedImage {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (record.image && typeof record.image === "object") return record.image as GeneratedImage;
    if (record.generated_image && typeof record.generated_image === "object") {
      return record.generated_image as GeneratedImage;
    }
  }
  return unwrapObject<GeneratedImage>(payload, ["data"]);
}

export const generationApi = {
  async list(): Promise<GeneratedImage[]> {
    const payload = await request<unknown>(API.generation.list);
    return unwrapList<GeneratedImage>(payload, ["images", "items", "data", "results"]);
  },

  async get(id: string): Promise<GeneratedImage> {
    const payload = await request<unknown>(API.generation.one(id));
    return asImage(payload);
  },

  async create(prompt: string, options?: GenerationCreateOptions): Promise<GeneratedImage> {
    const body: Record<string, string | boolean> = { prompt };
    if (options?.product_id) body.product_id = options.product_id;
    if (options?.offer_id) body.offer_id = options.offer_id;
    if (options?.festival) body.festival = options.festival;
    if (options?.use_canva) body.use_canva = true;
    const payload = await request<unknown>(API.generation.create, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return asImage(payload);
  },

  async reject(id: string): Promise<GeneratedImage> {
    const payload = await request<unknown>(API.generation.reject(id), { method: "POST" });
    return asImage(payload);
  },

  async regenerate(id: string): Promise<GeneratedImage> {
    const payload = await request<unknown>(API.generation.regenerate(id), { method: "POST" });
    return asImage(payload);
  },

  async approve(id: string): Promise<{ image: GeneratedImage; taskId?: string }> {
    const payload = await request<unknown>(API.generation.approve(id), { method: "POST" });
    const image = asImage(payload);
    const record = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
    const post = record.post && typeof record.post === "object" ? (record.post as Record<string, unknown>) : {};
    const taskId =
      (typeof record.task_id === "string" && record.task_id) ||
      (typeof post.task_id === "string" && post.task_id) ||
      undefined;
    return { image, taskId };
  },
};

/** @deprecated Alias kept for older call sites; maps onto generation records. */
export const contentJobsApi = {
  async list(): Promise<ContentJob[]> {
    return generationApi.list();
  },
  async get(id: string): Promise<ContentJob> {
    return generationApi.get(id);
  },
  async create(prompt: string): Promise<ContentJob> {
    return generationApi.create(prompt);
  },
  async approve(id: string): Promise<ContentJob> {
    const result = await generationApi.approve(id);
    return { ...result.image, task_id: result.taskId };
  },
  async reject(id: string): Promise<ContentJob> {
    return generationApi.reject(id);
  },
};
