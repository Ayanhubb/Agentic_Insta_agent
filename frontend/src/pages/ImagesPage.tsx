import { useEffect, useState } from "react";
import { StatusBadge } from "../components/ui/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/States";
import { formatDate, imageSrc } from "../lib/format";
import { generationApi, userMessageFor } from "../services/api";
import type { GeneratedImage } from "../types/api";

export function ImagesPage() {
  const [images, setImages] = useState<GeneratedImage[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    generationApi
      .list()
      .then(setImages)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!images) return <LoadingState label="Loading images…" />;
  if (!images.length) {
    return (
      <div className="page">
        <div className="page-header">
          <div>
            <h1>Images</h1>
            <p className="lede">Generated assets for this account only.</p>
          </div>
        </div>
        <EmptyState title="No generated images yet" body="Use the AI Generator to create a preview." />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Images</h1>
          <p className="lede">Generated assets for this account only.</p>
        </div>
      </div>
      <div className="image-grid">
        {images.map((image) => {
          const src = imageSrc(image.preview_url || image.image_url, image.filename, image.id);
          return (
            <article key={image.id} className="card image-card">
              {src ? <img src={src} alt="" /> : <div className="preview-frame">No preview</div>}
              <p>{image.original_prompt || image.enhanced_prompt || "Generated image"}</p>
              <p className="muted">Source: {image.source || "USER_PROMPT"}</p>
              <p className="faint">Created {formatDate(image.created_at)}</p>
              <div className="row">
                <StatusBadge value={image.approval_status || "PENDING"} />
                <StatusBadge value={image.publication_status || "GENERATED"} />
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
