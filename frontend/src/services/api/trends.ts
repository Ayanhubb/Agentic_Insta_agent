import { API } from "./endpoints";
import { request } from "./client";
import { stripSecrets } from "../../lib/trends";
import type {
  AccountPerformance,
  DailyReport,
  OpportunityItem,
  TrendFilters,
  TrendItem,
} from "../../lib/trends";

function withFilters(path: string, filters: TrendFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.industry) params.set("industry", filters.industry);
  if (filters.region) params.set("region", filters.region);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export const trendsApi = {
  account() {
    return request<{ account: AccountPerformance }>(API.trends.account).then(stripSecrets);
  },
  research(filters: TrendFilters = {}) {
    return request<{ trends: TrendItem[] }>(withFilters(API.trends.research, filters)).then(stripSecrets);
  },
  opportunities(filters: TrendFilters = {}) {
    return request<{ opportunities: OpportunityItem[] }>(withFilters(API.trends.opportunities, filters)).then(stripSecrets);
  },
  report(filters: TrendFilters = {}) {
    return request<{ report: DailyReport }>(withFilters(API.trends.reports, filters)).then(stripSecrets);
  },
  dismiss(id: string) {
    return request<{ opportunity: OpportunityItem }>(API.trends.dismiss(id), { method: "POST" }).then(stripSecrets);
  },
  save(id: string) {
    return request<{ opportunity: OpportunityItem }>(API.trends.save(id), { method: "POST" }).then(stripSecrets);
  },
  evidence(id: string) {
    return request<{ opportunity_id: string; evidence: TrendItem["evidence"]; note: string }>(API.trends.evidence(id)).then(
      stripSecrets,
    );
  },
  createContent(id: string) {
    return request<{
      opportunity_id: string;
      path: string;
      prompt: string;
      festival: string;
      product: string;
      kind: string;
      disclaimer: string;
    }>(API.trends.createContent(id), { method: "POST" }).then(stripSecrets);
  },
};
