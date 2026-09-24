import { useEffect, useState } from "react";
import { Card } from "../components/ui/Card";
import { ErrorState, LoadingState } from "../components/ui/States";
import { providersApi, userMessageFor } from "../services/api";
import type { PublicMcpStatus } from "../lib/providerStatus";

export function McpPage() {
  const [status, setStatus] = useState<PublicMcpStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    providersApi
      .mcp()
      .then(setStatus)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!status) return <LoadingState label="Loading MCP status…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>MCP</h1>
          <p className="lede">Tool availability for this account. MCP does not publish to Instagram.</p>
        </div>
      </div>
      <div className="grid stats">
        <article className="card">
          <p className="muted">MCP</p>
          <strong className="stat-value">{status.enabled}</strong>
        </article>
        <article className="card">
          <p className="muted">Canva</p>
          <strong className="stat-value">{status.canva}</strong>
        </article>
      </div>
      <Card title="Tools" className="mt">
        {status.tools.length ? (
          <ul className="stack">
            {status.tools.map((tool) => (
              <li key={tool.name} className="row">
                <span>{tool.name}</span>
                <span className="faint">{tool.available ? "Available" : "Unavailable"}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No MCP tools reported.</p>
        )}
        <p className="faint">Tokens and API keys are not shown here.</p>
      </Card>
    </div>
  );
}
