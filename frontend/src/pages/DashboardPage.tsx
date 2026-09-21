import { useEffect, useState } from "react";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { formatDate, imageSrc, isPublished, isSameDay, isSameMonth } from "../lib/format";
import { dashboardApi, userMessageFor, type DashboardData } from "../services/api";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <article className="card">
      <p className="muted">{label}</p>
      <strong className="stat-value">{value}</strong>
    </article>
  );
}

function festivalName(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const record = value as { festival_name?: string; name?: string };
  return String(record.festival_name || record.name || "");
}

function festivalDate(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const record = value as { festival_date?: string; date?: string };
  return String(record.festival_date || record.date || "");
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setError(null);
    dashboardApi
      .load()
      .then(setData)
      .catch((err) => setError(userMessageFor(err)));
  }

  useEffect(() => {
    let cancelled = false;
    dashboardApi
      .load()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(userMessageFor(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <LoadingState label="Loading dashboard…" />;

  const payload = data.payload;
  const posts = data.posts;
  const images = data.images;
  const totalPosts = payload?.total_posts ?? posts.length;
  const publishedPosts =
    payload?.published_posts ?? posts.filter((post) => isPublished(post.status)).length;
  const todaysPosts =
    payload?.todays_posts ??
    posts.filter((post) => isPublished(post.status) && isSameDay(post.published_at)).length;
  const monthlyPosts =
    payload?.monthly_posts ??
    posts.filter((post) => isPublished(post.status) && isSameMonth(post.published_at)).length;
  const generatedImages = payload?.generated_images ?? images.length;
  const festivalPosts =
    payload?.festival_posts ??
    posts.filter((post) => {
      const source = `${post.source ?? ""} ${post.post_type ?? ""}`.toUpperCase();
      return source.includes("FESTIVAL");
    }).length;
  const dailyOn = payload?.daily_automation ?? Boolean(data.automation?.daily_enabled);
  const festivalOn = payload?.festival_automation ?? Boolean(data.automation?.festival_enabled);
  const instagramConnected = payload?.instagram_connected ?? Boolean(data.instagram?.connected);
  const upcoming = payload?.upcoming_festival ?? data.campaigns[0] ?? null;
  const nextScheduled = payload?.next_scheduled_post ?? data.automation?.next_run_at ?? data.automation?.daily_post_time;
  const activity = payload?.recent_activity ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p className="lede">Publication counts, automation, and Instagram connection for this account.</p>
        </div>
      </div>
      <div className="grid stats">
        <Stat label="Total posts" value={totalPosts} />
        <Stat label="Today's posts" value={todaysPosts} />
        <Stat label="Monthly posts" value={monthlyPosts} />
        <Stat label="Generated images" value={generatedImages} />
        <Stat label="Published posts" value={publishedPosts} />
        <Stat label="Festival posts" value={festivalPosts} />
      </div>
      <div className="grid stats" style={{ marginTop: 16 }}>
        <Stat label="Daily automation" value={dailyOn ? "On" : "Off"} />
        <Stat label="Festival automation" value={festivalOn ? "On" : "Off"} />
        <article className="card">
          <p className="muted">Instagram connection</p>
          <strong className="stat-value">{instagramConnected ? "Connected" : "Not connected"}</strong>
          {data.instagram?.username ? <p className="faint">{data.instagram.username}</p> : null}
        </article>
      </div>
      <div className="grid two" style={{ marginTop: 20 }}>
        <Card title="Upcoming festival">
          {festivalName(upcoming) ? (
            <div>
              <p>{festivalName(upcoming)}</p>
              <p className="faint">{festivalDate(upcoming) || "Date not set"}</p>
            </div>
          ) : (
            <p className="muted">No upcoming festival</p>
          )}
        </Card>
        <Card title="Next scheduled post">
          {nextScheduled ? (
            <p>{nextScheduled} <span className="faint">Asia/Kolkata</span></p>
          ) : (
            <p className="muted">No scheduled post</p>
          )}
        </Card>
      </div>
      <Card title="Recent activity" className="mt">
        {activity.length ? (
          <ul className="stack">
            {activity.map((item, index) => (
              <li key={`${item.label}-${item.at}-${index}`} className="row">
                <span>{item.label || "Update"}</span>
                <span className="faint">{formatDate(item.at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No recent activity</p>
        )}
      </Card>
      <Card title="Recent images" className="mt">
        {images.length ? (
          <ul className="stack">
            {images.slice(0, 6).map((image) => {
              const src = imageSrc(image.preview_url || image.image_url, image.filename, image.id);
              return (
                <li key={image.id} className="row">
                  {src ? <img className="thumb" src={src} alt="" /> : null}
                  <span>{image.original_prompt || "Generated image"}</span>
                  <StatusBadge value={image.approval_status || image.publication_status} />
                </li>
              );
            })}
          </ul>
        ) : (
          <EmptyState title="No images yet" body="Generate a preview from the AI Generator." />
        )}
      </Card>
      <Card title="Recent posts" className="mt">
        {posts.length ? (
          <ul className="stack">
            {posts.slice(0, 6).map((post) => (
              <li key={post.id} className="row">
                <StatusBadge value={post.status} />
                <span>{post.source || post.post_type || "post"}</span>
                <span className="faint">{formatDate(post.published_at || post.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No posts yet</p>
        )}
      </Card>
    </div>
  );
}
