import { automationApi } from "./automation";
import { festivalsApi } from "./festivals";
import { generationApi } from "./generation";
import { instagramApi } from "./instagram";
import { postsApi } from "./posts";
import { isNotImplemented } from "./errors";
import { request } from "./client";
import { API } from "./endpoints";
import type {
  AutomationSettings,
  FestivalCampaign,
  GeneratedImage,
  InstagramPost,
  InstagramStatus,
} from "../../types/api";

export interface DashboardPayload {
  total_posts?: number;
  todays_posts?: number;
  monthly_posts?: number;
  generated_images?: number;
  published_posts?: number;
  festival_posts?: number;
  daily_automation?: boolean;
  festival_automation?: boolean;
  instagram_connected?: boolean;
  recent_images?: GeneratedImage[];
  recent_posts?: InstagramPost[];
  upcoming_festival?: FestivalCampaign | Record<string, unknown> | null;
  next_scheduled_post?: string | null;
  recent_activity?: { label?: string; at?: string }[];
}

export interface DashboardData {
  payload: DashboardPayload | null;
  posts: InstagramPost[];
  images: GeneratedImage[];
  automation: AutomationSettings | null;
  campaigns: FestivalCampaign[];
  instagram: InstagramStatus | null;
  unavailable: string[];
}

async function optional<T>(label: string, fn: () => Promise<T>, unavailable: string[]): Promise<T | null> {
  try {
    return await fn();
  } catch (error) {
    if (isNotImplemented(error)) {
      unavailable.push(label);
      return null;
    }
    throw error;
  }
}

export const dashboardApi = {
  async load(): Promise<DashboardData> {
    const unavailable: string[] = [];
    const payload = await optional("dashboard", () => request<DashboardPayload>(API.dashboard), unavailable);
    if (payload) {
      return {
        payload,
        posts: payload.recent_posts ?? [],
        images: payload.recent_images ?? [],
        automation: {
          daily_enabled: payload.daily_automation,
          festival_enabled: payload.festival_automation,
          next_run_at: payload.next_scheduled_post,
        },
        campaigns: payload.upcoming_festival
          ? [payload.upcoming_festival as FestivalCampaign]
          : [],
        instagram: { connected: payload.instagram_connected },
        unavailable,
      };
    }

    const [posts, images, automation, campaigns, instagram] = await Promise.all([
      optional("posts", () => postsApi.list(), unavailable),
      optional("images", () => generationApi.list(), unavailable),
      optional("automation", () => automationApi.get(), unavailable),
      optional("festivals", () => festivalsApi.campaigns(), unavailable),
      optional("instagram", () => instagramApi.status(), unavailable),
    ]);

    return {
      payload: null,
      posts: posts ?? [],
      images: images ?? [],
      automation,
      campaigns: campaigns ?? [],
      instagram,
      unavailable,
    };
  },
};
