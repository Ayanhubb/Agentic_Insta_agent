export type KnowledgeKind = "observed" | "discovered" | "inferred" | "recommended";

export const KIND_LABEL: Record<KnowledgeKind, string> = {
  observed: "OBSERVED DATA",
  discovered: "RESEARCH SIGNAL",
  inferred: "AI INTERPRETATION",
  recommended: "CONTENT RECOMMENDATION",
};

const SECRET_FRAGMENTS = ["token", "api_key", "apikey", "secret", "password", "authorization", "credential"];

export function kindLabel(kind: string | undefined): string {
  if (kind && kind in KIND_LABEL) return KIND_LABEL[kind as KnowledgeKind];
  return KIND_LABEL.discovered;
}

export function stripSecrets<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map((item) => stripSecrets(item)) as T;
  }
  if (value && typeof value === "object") {
    const cleaned: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      const lowered = key.toLowerCase();
      if (SECRET_FRAGMENTS.some((fragment) => lowered.includes(fragment))) continue;
      cleaned[key] = stripSecrets(item);
    }
    return cleaned as T;
  }
  return value;
}

export function isExpired(expiresAt: string | null | undefined, now = Date.now()): boolean {
  if (!expiresAt) return false;
  const parsed = Date.parse(expiresAt);
  return Number.isFinite(parsed) && parsed < now;
}

export interface TrendEvidence {
  kind: KnowledgeKind | string;
  text: string;
  source: string;
  observed_at?: string | null;
}

export interface TrendItem {
  id: string;
  title: string;
  source: string;
  observed_at: string | null;
  freshness: string;
  industry: string;
  region: string;
  evidence: TrendEvidence[];
  confidence: string;
  expires_at: string | null;
  expired: boolean;
  kind: KnowledgeKind | string;
}

export interface OpportunityItem {
  id: string;
  title: string;
  why_now: string;
  evidence: TrendEvidence[];
  product: string;
  festival: string;
  recommended_format: string;
  creative_direction: string;
  expires_at: string | null;
  expired: boolean;
  confidence: string;
  industry: string;
  region: string;
  status: string;
  kind: "recommended";
  disclaimer: string;
}

export interface AccountPerformance {
  kind: "observed";
  recent_posts: Array<{
    id: string;
    status: string;
    post_type: string;
    created_at: string | null;
    published_at: string | null;
    has_error: boolean;
  }>;
  posting_frequency: {
    kind: "observed";
    published_last_7_days: number;
    published_prior_7_days: number;
    posts_per_week: number;
  };
  engagement: {
    kind: "observed";
    available: boolean;
    metrics: Record<string, number>;
    note: string;
  };
  top_performing: {
    kind: "observed";
    available: boolean;
    items: Array<Record<string, unknown>>;
    note: string;
  };
  content_mix: { kind: "observed"; counts: Record<string, number> };
  performance_changes: { kind: "observed"; published_delta: number; note: string };
}

export interface DailyReport {
  account_summary: { kind: KnowledgeKind | string; text: string };
  trend_summary: { kind: KnowledgeKind | string; text: string };
  festival_opportunities: OpportunityItem[];
  product_opportunities: OpportunityItem[];
  content_queue: Array<{
    id: string;
    title: string;
    kind: KnowledgeKind | string;
    status: string;
    disclaimer?: string;
  }>;
}

export interface TrendFilters {
  industry?: string;
  region?: string;
}
