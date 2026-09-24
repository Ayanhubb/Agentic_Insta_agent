import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/ui/Card";
import { ProgressBar } from "../components/ui/ProgressBar";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { festivalsApi, userMessageFor } from "../services/api";
import type { FestivalCampaign } from "../types/api";

export function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<FestivalCampaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    festivalsApi
      .campaigns()
      .then(setCampaigns)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!campaigns) return <LoadingState label="Loading campaigns…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Campaigns</h1>
          <p className="lede">Festival campaigns for this account. Generate opens the approval flow.</p>
        </div>
      </div>
      {!campaigns.length ? (
        <EmptyState title="No campaigns yet" body="Festival campaigns appear after the calendar is loaded for this account." />
      ) : (
        <Card>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Festival</th>
                  <th>Date</th>
                  <th>Published</th>
                  <th>Remaining</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {campaigns.map((item) => {
                  const name = item.festival_name || item.name || "Campaign";
                  const required = item.required_posts ?? 0;
                  const published = item.published_posts ?? 0;
                  const remaining = item.remaining_posts ?? Math.max(0, required - published);
                  return (
                    <tr key={item.id}>
                      <td>{name}</td>
                      <td>{item.festival_date || item.date || "—"}</td>
                      <td>{published}</td>
                      <td>{remaining}</td>
                      <td>
                        <StatusBadge value={item.status || (remaining === 0 ? "completed" : "active")} />
                      </td>
                      <td style={{ minWidth: 120 }}>
                        <ProgressBar value={published} max={required || 1} />
                      </td>
                      <td>
                        <Link to={`/generate?festival=${encodeURIComponent(name)}`}>Generate</Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
