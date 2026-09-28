import { useEffect, useState } from "react";
import { Card } from "../../components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/States";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { agentsApi, userMessageFor } from "../../services/api";
import type { AgentExecution } from "../../types/agents";
import { useAgentConfig } from "./AgentLayout";

export function AgentExecutionsPage() {
  const { agent } = useAgentConfig();
  const [items, setItems] = useState<AgentExecution[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    agentsApi
      .executions(agent.key)
      .then(setItems)
      .catch((err) => setError(userMessageFor(err)));
  }, [agent.key]);

  if (error) return <ErrorState message={error} />;
  if (!items) return <LoadingState label="Loading executions…" />;

  return (
    <Card title="Executions">
      <p className="muted">
        Runs recorded for this configuration. Instagram studio tasks stay on the studio task history.
      </p>
      {items.length === 0 ? (
        <EmptyState title="No executions" body="This agent has no recorded runs yet." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Workflow</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.execution_id}>
                <td>{item.execution_id}</td>
                <td>
                  <StatusBadge value={item.status} />
                </td>
                <td>{item.trigger_key}</td>
                <td>{item.workflow_key}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
