import { useEffect, useState } from "react";
import { isNotImplemented, providersApi, userMessageFor } from "../../services/api";
import type { PublicProviderStatus } from "../../lib/providerStatus";
import { ErrorState, LoadingState } from "../ui/States";

function StatusLine({ label, value }: { label: string; value: string }) {
  return (
    <p className="row">
      <span>{label}</span>
      <strong>{value}</strong>
    </p>
  );
}

export function ProviderStatus({ showProviders = true }: { showProviders?: boolean }) {
  const [status, setStatus] = useState<PublicProviderStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    providersApi
      .ai()
      .then(setStatus)
      .catch((err) => {
        if (isNotImplemented(err)) {
          setStatus({
            deepseek: "Not configured",
            openai: "Not configured",
            canva: "Not connected",
            mcp: "Not available",
            llmProvider: null,
            imageProvider: null,
            visionProvider: null,
          });
          return;
        }
        setError(userMessageFor(err));
      });
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!status) return <LoadingState label="Loading AI provider status…" />;

  return (
    <div className="stack">
      {showProviders ? (
        <>
          <StatusLine label="DeepSeek" value={status.deepseek} />
          <StatusLine label="OpenAI" value={status.openai} />
          <StatusLine label="Canva" value={status.canva} />
        </>
      ) : null}
      {status.llmProvider ? <p className="faint">LLM provider: {status.llmProvider}</p> : null}
      {status.imageProvider ? <p className="faint">Image provider: {status.imageProvider}</p> : null}
      {status.visionProvider ? <p className="faint">Vision provider: {status.visionProvider}</p> : null}
      <p className="faint">API keys, access tokens, and encryption secrets stay on the server.</p>
    </div>
  );
}
