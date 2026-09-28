import { Card } from "../../components/ui/Card";
import { REFERENCE_WORKFLOW, currentVersion } from "../../lib/agentCatalog";
import { useAgentConfig } from "./AgentLayout";

function label(value: string): string {
  return value.replaceAll("_", " ");
}

export function AgentWorkflowPage() {
  const { agent } = useAgentConfig();
  const version = currentVersion(agent);
  const workflowId = version?.workflow_key || REFERENCE_WORKFLOW.id;
  const known = workflowId === REFERENCE_WORKFLOW.id;

  return (
    <Card title="Workflow">
      <div className="stack">
        <p className="muted">
          A workflow is a registered sequence. Steps are not editable, and Instagram publish stays inside the reference agent.
        </p>
        <p>
          <strong>{known ? REFERENCE_WORKFLOW.label : workflowId}</strong>
        </p>
        <p className="muted">{known ? REFERENCE_WORKFLOW.summary : "This workflow key is stored on the agent."}</p>
        {known ? (
          <ol className="stack">
            {REFERENCE_WORKFLOW.steps.map((step) => (
              <li key={step.step_key}>
                {step.position}. {label(step.step_key)}{" "}
                <span className="faint">
                  {label(step.kind)} · {label(step.side_effect)}
                </span>
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </Card>
  );
}
