import { useEffect, useState } from "react";
import { StatusBadge } from "../components/ui/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { formatDate, imageSrc } from "../lib/format";
import { postsApi, userMessageFor } from "../services/api";
import type { InstagramPost } from "../types/api";

export function PostsPage() {
  const [posts, setPosts] = useState<InstagramPost[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    postsApi
      .list()
      .then(setPosts)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!posts) return <LoadingState label="Loading posts…" />;
  if (!posts.length) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <h1>Posts</h1>
            <p className="lede">Publication history for this Instagram account.</p>
          </div>
        </div>
        <EmptyState title="No posts yet" body="Approved images appear here after the Instagram Agent runs." />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Posts</h1>
          <p className="lede">Publication history for this Instagram account.</p>
        </div>
      </div>
      <div className="table-wrap card">
        <table>
          <thead>
            <tr>
              <th>Image</th>
              <th>Type</th>
              <th>Source</th>
              <th>Status</th>
              <th>Instagram media ID</th>
              <th>Published date</th>
              <th>Permalink</th>
            </tr>
          </thead>
          <tbody>
            {posts.map((post) => {
              const src = imageSrc(post.preview_url || post.image_url, null, post.generated_image_id);
              return (
                <tr key={post.id}>
                  <td>{src ? <img className="thumb" src={src} alt="" /> : "—"}</td>
                  <td>{post.post_type || "IMAGE"}</td>
                  <td>{post.source || "—"}</td>
                  <td>
                    <StatusBadge value={post.status} />
                  </td>
                  <td>{post.instagram_media_id || "—"}</td>
                  <td>{formatDate(post.published_at)}</td>
                  <td>
                    {post.permalink ? (
                      <a href={post.permalink} target="_blank" rel="noreferrer">
                        Open
                      </a>
                    ) : (
                      "—"
                    )}
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
