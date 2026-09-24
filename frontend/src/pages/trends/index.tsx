import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { Button } from "../../components/ui/Button";
import { Field, Select } from "../../components/ui/Field";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/States";
import { formatDate } from "../../lib/format";
import { isExpired, kindLabel } from "../../lib/trends";
import type {
  AccountPerformance,
  DailyReport,
  OpportunityItem,
  TrendEvidence,
  TrendFilters,
  TrendItem,
} from "../../lib/trends";
import { trendsApi, userMessageFor } from "../../services/api";

const SUBNAV = [
  { to: "/trends/account", label: "Account performance" },
  { to: "/trends/research", label: "Current trends" },
  { to: "/trends/opportunities", label: "Content opportunities" },
  { to: "/trends/reports", label: "Daily report" },
];

function matchesCompact(): boolean {
  if (typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(max-width: 820px)").matches;
}

function useCompactLayout(): boolean {
  const [compact, setCompact] = useState(matchesCompact);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 820px)");
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return compact;
}

export function useTrendFilters(): TrendFilters & {
  setFilter: (key: "industry" | "region", value: string) => void;
} {
  const [params, setParams] = useSearchParams();
  const industry = params.get("industry") ?? "";
  const region = params.get("region") ?? "";
  function setFilter(key: "industry" | "region", value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }
  return { industry, region, setFilter };
}

function KindBadge({ kind }: { kind: string | undefined }) {
  return <span className={`kind-badge kind-${kind || "discovered"}`}>{kindLabel(kind)}</span>;
}

function unique(values: Array<string | undefined>): string[] {
  return [...new Set(values.map((value) => value?.trim() || "").filter(Boolean))].sort();
}

export function TrendFiltersBar({
  industries,
  regions,
}: {
  industries: string[];
  regions: string[];
}) {
  const { industry, region, setFilter } = useTrendFilters();
  const industryOptions = unique([industry, ...industries]);
  const regionOptions = unique([region, ...regions]);
  return (
    <div className="trend-filters">
      <Field label="Industry">
        <Select value={industry} onChange={(event) => setFilter("industry", event.target.value)}>
          <option value="">All industries</option>
          {industryOptions.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Region">
        <Select value={region} onChange={(event) => setFilter("region", event.target.value)}>
          <option value="">All regions</option>
          {regionOptions.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </Select>
      </Field>
    </div>
  );
}

function EvidenceList({ items }: { items: TrendEvidence[] }) {
  if (!items.length) return <p className="muted">No evidence recorded.</p>;
  return (
    <ul className="trend-evidence">
      {items.map((item, index) => (
        <li key={`${item.source}-${index}`}>
          <KindBadge kind={item.kind} />
          <p>{item.text}</p>
          {item.source ? <p className="muted">Source: {item.source}</p> : null}
          {item.observed_at ? <p className="muted">Observed: {formatDate(item.observed_at)}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function trendExpired(trend: TrendItem): boolean {
  return Boolean(trend.expired) || isExpired(trend.expires_at);
}

function TrendCard({ trend }: { trend: TrendItem }) {
  const expired = trendExpired(trend);
  return (
    <article className="card trend-card">
      <KindBadge kind={trend.kind} />
      <h3>{trend.title}</h3>
      <dl className="trend-facts">
        <div>
          <dt>Source</dt>
          <dd>{trend.source || "—"}</dd>
        </div>
        <div>
          <dt>Observed time</dt>
          <dd>{formatDate(trend.observed_at)}</dd>
        </div>
        <div>
          <dt>Freshness</dt>
          <dd>{expired ? "Expired" : trend.freshness || "unknown"}</dd>
        </div>
        <div>
          <dt>Industry</dt>
          <dd>{trend.industry || "—"}</dd>
        </div>
        <div>
          <dt>Region</dt>
          <dd>{trend.region || "—"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{trend.confidence || "—"}</dd>
        </div>
        <div>
          <dt>Expiration</dt>
          <dd>{trend.expires_at ? formatDate(trend.expires_at) : "No expiration"}</dd>
        </div>
      </dl>
      <h4>Evidence</h4>
      <EvidenceList items={trend.evidence || []} />
    </article>
  );
}

export function TrendsLayout() {
  const compact = useCompactLayout();
  return (
    <AppShell>
      <div className="page trends-page" data-layout={compact ? "stack" : "wide"}>
        <header className="page-header">
          <div>
            <p className="eyebrow">Trend intelligence</p>
            <h1>Trend Intelligence</h1>
          </div>
        </header>
        <nav className="trends-subnav" aria-label="Trend sections">
          <NavLink to="/trends" end className={({ isActive }) => (isActive ? "active" : undefined)}>
            Overview
          </NavLink>
          {SUBNAV.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : undefined)}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </div>
    </AppShell>
  );
}

function useAsync<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((body) => {
        if (!cancelled) setData(body);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setData(null);
          setError(userMessageFor(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // loader identity changes every render; callers pass deps explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadKey]);

  return {
    data,
    error,
    loading,
    reload: () => setReloadKey((value) => value + 1),
  };
}

export function TrendsOverviewPage() {
  const { industry, region } = useTrendFilters();
  const filters = { industry, region };
  const { data, error, loading, reload } = useAsync(async () => {
    const [account, research, opportunities, report] = await Promise.all([
      trendsApi.account(),
      trendsApi.research(filters),
      trendsApi.opportunities(filters),
      trendsApi.report(filters),
    ]);
    return { account: account.account, research: research.trends, opportunities: opportunities.opportunities, report: report.report };
  }, [industry, region]);

  if (loading) return <LoadingState label="Loading trends" />;
  if (error || !data) return <ErrorState message={error || "Trend intelligence is unavailable."} onRetry={reload} />;

  const current = data.research.filter((item) => !trendExpired(item));
  return (
    <div className="trend-grid">
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Account performance</h2>
        <p>{data.report.account_summary.text}</p>
        <p className="muted">{data.account.performance_changes.note}</p>
      </section>
      <section className="card">
        <KindBadge kind="discovered" />
        <h2>Current trends</h2>
        <p>{current.length ? `${current.length} current trend(s).` : "No trends yet."}</p>
      </section>
      <section className="card">
        <KindBadge kind="recommended" />
        <h2>Content opportunities</h2>
        <p className="disclaimer">Recommendations are not observed facts.</p>
        <p>{data.opportunities.length ? `${data.opportunities.length} open recommendation(s).` : "No content opportunities yet."}</p>
      </section>
      <section className="card">
        <h2>Daily report</h2>
        <KindBadge kind={data.report.trend_summary.kind} />
        <p>{data.report.trend_summary.text}</p>
      </section>
    </div>
  );
}

export function AccountTrendsPage() {
  const { data, error, loading, reload } = useAsync(() => trendsApi.account().then((body) => body.account), []);
  if (loading) return <LoadingState label="Loading trends" />;
  if (error || !data) return <ErrorState message={error || "Account performance is unavailable."} onRetry={reload} />;
  return <AccountPanel account={data} />;
}

function AccountPanel({ account }: { account: AccountPerformance }) {
  const mix = Object.entries(account.content_mix?.counts || {});
  return (
    <div className="stack">
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Recent posts</h2>
        {account.recent_posts.length ? (
          <ul className="trend-list">
            {account.recent_posts.map((post) => (
              <li key={post.id}>
                <strong>{post.post_type}</strong>
                <span className="muted">{post.status}</span>
                <span>{formatDate(post.published_at || post.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No posts yet" body="Recent posts appear here after this account publishes through the studio." />
        )}
      </section>
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Posting frequency</h2>
        <p>{account.posting_frequency.published_last_7_days} published in the last 7 days.</p>
        <p className="muted">{account.posting_frequency.published_prior_7_days} published in the previous 7 days.</p>
      </section>
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Engagement metrics</h2>
        {account.engagement.available && Object.keys(account.engagement.metrics).length ? (
          <ul>
            {Object.entries(account.engagement.metrics).map(([name, value]) => (
              <li key={name}>
                {name}: {value}
              </li>
            ))}
          </ul>
        ) : (
          <p>{account.engagement.note}</p>
        )}
      </section>
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Top-performing content</h2>
        {account.top_performing.available && account.top_performing.items.length ? (
          <ul>
            {account.top_performing.items.map((item, index) => (
              <li key={index}>{String(item.title || item.id || "Post")}</li>
            ))}
          </ul>
        ) : (
          <p>{account.top_performing.note}</p>
        )}
      </section>
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Content mix</h2>
        {mix.length ? (
          <ul>
            {mix.map(([name, count]) => (
              <li key={name}>
                {name}: {count}
              </li>
            ))}
          </ul>
        ) : (
          <p>No posts are stored for this account yet.</p>
        )}
      </section>
      <section className="card">
        <KindBadge kind="observed" />
        <h2>Performance changes</h2>
        <p>{account.performance_changes.note}</p>
      </section>
    </div>
  );
}

export function ResearchPage() {
  const { industry, region } = useTrendFilters();
  const { data, error, loading, reload } = useAsync(
    () => trendsApi.research({ industry, region }).then((body) => body.trends),
    [industry, region],
  );
  if (loading) return <LoadingState label="Loading trends" />;
  if (error || !data) return <ErrorState message={error || "Research is unavailable."} onRetry={reload} />;
  const current = data.filter((item) => !trendExpired(item));
  const expired = data.filter((item) => trendExpired(item));
  return (
    <div className="stack">
      <TrendFiltersBar industries={unique(data.map((item) => item.industry))} regions={unique(data.map((item) => item.region))} />
      <section>
        <h2>Current trends</h2>
        {current.length ? (
          <div className="trend-grid">
            {current.map((trend) => (
              <TrendCard key={trend.id} trend={trend} />
            ))}
          </div>
        ) : (
          <EmptyState title="No trends yet" body="Research signals show up here when they are stored for this account." />
        )}
      </section>
      <section>
        <h2>Expired trends</h2>
        {expired.length ? (
          <div className="trend-grid">
            {expired.map((trend) => (
              <TrendCard key={trend.id} trend={trend} />
            ))}
          </div>
        ) : (
          <p className="muted">No expired trends in this view.</p>
        )}
      </section>
    </div>
  );
}

export function OpportunitiesPage() {
  const navigate = useNavigate();
  const { industry, region } = useTrendFilters();
  const { data, error, loading, reload } = useAsync(
    () => trendsApi.opportunities({ industry, region }).then((body) => body.opportunities),
    [industry, region],
  );
  const [items, setItems] = useState<OpportunityItem[] | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<{ id: string; note: string; items: TrendEvidence[] } | null>(null);

  useEffect(() => {
    setItems(data);
  }, [data]);

  const visible = items ?? data;
  if (loading) return <LoadingState label="Loading trends" />;
  if (error || !visible) return <ErrorState message={error || "Content opportunities are unavailable."} onRetry={reload} />;

  async function dismiss(id: string) {
    setActionError(null);
    try {
      await trendsApi.dismiss(id);
      setItems((current) => (current ?? data ?? []).filter((item) => item.id !== id));
    } catch (err) {
      setActionError(userMessageFor(err));
    }
  }

  async function save(id: string) {
    setActionError(null);
    try {
      const body = await trendsApi.save(id);
      setItems((current) => (current ?? data ?? []).map((item) => (item.id === id ? body.opportunity : item)));
    } catch (err) {
      setActionError(userMessageFor(err));
    }
  }

  async function viewEvidence(id: string) {
    setActionError(null);
    try {
      const body = await trendsApi.evidence(id);
      setEvidence({ id, note: body.note, items: body.evidence || [] });
    } catch (err) {
      setActionError(userMessageFor(err));
    }
  }

  async function createContent(item: OpportunityItem) {
    setActionError(null);
    try {
      const handoff = await trendsApi.createContent(item.id);
      const params = new URLSearchParams();
      if (handoff.prompt) params.set("prompt", handoff.prompt);
      if (handoff.festival) params.set("festival", handoff.festival);
      if (handoff.product) params.set("product", handoff.product);
      navigate(`/generate?${params.toString()}`);
    } catch (err) {
      setActionError(userMessageFor(err));
    }
  }

  return (
    <div className="stack">
      <TrendFiltersBar
        industries={unique(visible.map((item) => item.industry))}
        regions={unique(visible.map((item) => item.region))}
      />
      {actionError ? <ErrorState message={actionError} /> : null}
      <h2>Content opportunities</h2>
      {visible.length ? (
        <div className="trend-grid">
          {visible.map((item) => (
            <article key={item.id} className="card trend-card">
              <KindBadge kind="recommended" />
              <p className="disclaimer">{item.disclaimer}</p>
              <h3>{item.title}</h3>
              <h4>Why now</h4>
              <p>{item.why_now}</p>
              <h4>Evidence</h4>
              <EvidenceList items={item.evidence || []} />
              <dl className="trend-facts">
                <div>
                  <dt>Product</dt>
                  <dd>{item.product || "—"}</dd>
                </div>
                <div>
                  <dt>Festival</dt>
                  <dd>{item.festival || "—"}</dd>
                </div>
                <div>
                  <dt>Recommended format</dt>
                  <dd>{item.recommended_format || "—"}</dd>
                </div>
                <div>
                  <dt>Expiration</dt>
                  <dd>{item.expired ? "Expired" : item.expires_at ? formatDate(item.expires_at) : "No expiration"}</dd>
                </div>
                <div>
                  <dt>Confidence</dt>
                  <dd>{item.confidence || "—"}</dd>
                </div>
              </dl>
              <h4>Creative direction</h4>
              <p>{item.creative_direction}</p>
              <div className="trend-actions">
                <Button type="button" disabled={item.expired} onClick={() => void createContent(item)}>
                  Create Content
                </Button>
                <Button type="button" variant="secondary" onClick={() => void dismiss(item.id)}>
                  Dismiss
                </Button>
                <Button type="button" variant="secondary" onClick={() => void save(item.id)}>
                  Save
                </Button>
                <Button type="button" variant="ghost" onClick={() => void viewEvidence(item.id)}>
                  View Evidence
                </Button>
              </div>
              {item.status === "saved" ? <p className="muted">Saved for the content queue.</p> : null}
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title="No content opportunities yet" body="Recommendations appear here separately from observed account data." />
      )}
      {evidence ? (
        <section className="card" aria-label="Evidence">
          <h2>Evidence</h2>
          <p className="muted">{evidence.note}</p>
          <EvidenceList items={evidence.items} />
        </section>
      ) : null}
    </div>
  );
}

export function ReportsPage() {
  const { industry, region } = useTrendFilters();
  const { data, error, loading, reload } = useAsync(
    () => trendsApi.report({ industry, region }).then((body) => body.report),
    [industry, region],
  );
  if (loading) return <LoadingState label="Loading trends" />;
  if (error || !data) return <ErrorState message={error || "The daily report is unavailable."} onRetry={reload} />;
  return <ReportPanel report={data} />;
}

function ReportPanel({ report }: { report: DailyReport }) {
  return (
    <div className="stack">
      <section className="card">
        <h2>Account summary</h2>
        <KindBadge kind={report.account_summary.kind} />
        <p>{report.account_summary.text}</p>
      </section>
      <section className="card">
        <h2>Trend summary</h2>
        <KindBadge kind={report.trend_summary.kind} />
        <p>{report.trend_summary.text}</p>
      </section>
      <section className="card">
        <h2>Festival opportunities</h2>
        <KindBadge kind="recommended" />
        <OpportunityLines items={report.festival_opportunities} empty="No festival recommendations in this report." />
      </section>
      <section className="card">
        <h2>Product opportunities</h2>
        <KindBadge kind="recommended" />
        <OpportunityLines items={report.product_opportunities} empty="No product recommendations in this report." />
      </section>
      <section className="card">
        <h2>Content queue</h2>
        <KindBadge kind="recommended" />
        {report.content_queue.length ? (
          <ul className="trend-list">
            {report.content_queue.map((item) => (
              <li key={item.id}>
                <KindBadge kind={item.kind} />
                <span>{item.title}</span>
                {item.disclaimer ? <p className="disclaimer">{item.disclaimer}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">The content queue is empty.</p>
        )}
      </section>
    </div>
  );
}

function OpportunityLines({ items, empty }: { items: OpportunityItem[]; empty: string }) {
  if (!items.length) return <p className="muted">{empty}</p>;
  return (
    <ul className="trend-list">
      {items.map((item) => (
        <li key={item.id}>
          <strong>{item.title}</strong>
          <p className="disclaimer">{item.disclaimer}</p>
        </li>
      ))}
    </ul>
  );
}
