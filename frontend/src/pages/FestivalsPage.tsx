import { useEffect, useState } from "react";
import { ProgressBar } from "../components/ui/ProgressBar";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { festivalsApi, userMessageFor } from "../services/api";
import type { FestivalCampaign } from "../types/api";

export function FestivalsPage() {
  const [campaigns, setCampaigns] = useState<FestivalCampaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    festivalsApi
      .campaigns()
      .then(setCampaigns)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!campaigns) return <LoadingState label="Loading festivals…" />;
  if (!campaigns.length) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <h1>Festivals</h1>
            <p className="lede">Required posts, published count, and remaining work per campaign.</p>
          </div>
        </div>
        <EmptyState
          title="No festival campaigns"
          body="Enable festival automation to create campaigns from the India calendar."
        />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Festivals</h1>
          <p className="lede">Required posts, published count, and remaining work per campaign.</p>
        </div>
      </div>
      <div className="table-wrap card">
        <table>
          <thead>
            <tr>
              <th>Festival</th>
              <th>Date</th>
              <th>Required posts</th>
              <th>Generated</th>
              <th>Published</th>
              <th>Remaining</th>
              <th>Status</th>
              <th>Progress</th>
            </tr>
          </thead>
          <tbody>
            {campaigns.map((item) => {
              const required = item.required_posts ?? 0;
              const generated = item.generated_posts ?? 0;
              const published = item.published_posts ?? 0;
              const remaining = item.remaining_posts ?? Math.max(0, required - published);
              const status = item.status || (remaining === 0 ? "completed" : "active");
              return (
                <tr key={item.id}>
                  <td>{item.festival_name || item.name}</td>
                  <td>{item.festival_date || item.date || "—"}</td>
                  <td>{required}</td>
                  <td>{generated}</td>
                  <td>{published}</td>
                  <td>{remaining}</td>
                  <td>
                    <StatusBadge value={status} />
                  </td>
                  <td style={{ minWidth: 120 }}>
                    <ProgressBar value={published} max={required || 1} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
