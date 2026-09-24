import { useEffect, useState } from "react";
import { useToast } from "../../context/ToastContext";
import { assetMediaUrl, brandApi, isNotImplemented, userMessageFor } from "../../services/api";
import type { BrandAsset } from "../../types/api";
import { Button } from "../ui/Button";
import { StatusBadge } from "../ui/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "../ui/States";

export function AssetLibrary() {
  const { push } = useToast();
  const [assets, setAssets] = useState<BrandAsset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  function load() {
    brandApi
      .assets()
      .then((rows) => {
        setAssets(rows);
        setUnavailable(false);
      })
      .catch((err) => {
        if (isNotImplemented(err)) {
          setAssets([]);
          setUnavailable(true);
          return;
        }
        setError(userMessageFor(err));
      });
  }

  useEffect(() => {
    load();
  }, []);

  async function remove(id: string) {
    try {
      await brandApi.deleteAsset(id);
      push("Asset removed.", "success");
      load();
    } catch (err) {
      push(userMessageFor(err), "error");
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!assets) return <LoadingState label="Loading assets…" />;
  if (unavailable) {
    return <EmptyState title="Assets are not available yet" body="Logo and product files appear here once the brand asset API is mounted." />;
  }
  if (!assets.length) {
    return <EmptyState title="No assets yet" body="Upload a company logo or product image from Brand or Products." />;
  }

  return (
    <ul className="stack">
      {assets.map((asset) => {
        const src = assetMediaUrl(asset);
        return (
          <li key={asset.id} className="row">
            {src ? <img className="thumb" src={src} alt="" /> : null}
            <span>{asset.filename || asset.role || "Asset"}</span>
            <StatusBadge value={asset.role || asset.status} />
            <Button type="button" variant="ghost" onClick={() => void remove(asset.id)}>
              Delete
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
