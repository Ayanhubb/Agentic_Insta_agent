import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/States";
import { agentsApi, userMessageFor } from "../../services/api";
import type { PlatformAgent } from "../../types/agents";

export function AgentsListPage() {
  const [agents, setAgents] = useState<PlatformAgent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    agentsApi
      .list()
      .then(setAgents)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!agents) return <LoadingState label="Loading agents…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Agents</h1>
          <p className="lede">
            Create a configuration from registered workflows and tools. The Instagram studio stays the publisher.
          </p>
        </div>
        <Link className="btn" to="/agents/new">
          New agent
        </Link>
      </div>
      {agents.length === 0 ? (
        <EmptyState title="No agents" body="Create an agent to choose its models, tools, and approval policy." />
      ) : (
        <div className="grid cards">
          {agents.map((agent) => (
            <Link key={agent.key} to={`/agents/${agent.key}`} className="card agent-card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{agent.display_name}</strong>
                <StatusBadge value={agent.status} />
              </div>
              <p className="muted">{agent.description || "No description yet."}</p>
              <p className="faint">{agent.owner === "system" ? "System agent" : agent.key}</p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
