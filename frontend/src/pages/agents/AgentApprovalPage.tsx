import { useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field, Select } from "../../components/ui/Field";
import { useToast } from "../../context/ToastContext";
import { APPROVAL_POLICY, currentVersion } from "../../lib/agentCatalog";
import { agentsApi, userMessageFor } from "../../services/api";
import { useAgentConfig } from "./AgentLayout";

export function AgentApprovalPage() {
  const { agent, setAgent } = useAgentConfig();
  const { push } = useToast();
  const bound = currentVersion(agent)?.approval_policy_id ?? APPROVAL_POLICY.id;
  const [policyId, setPolicyId] = useState(bound);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const known = policyId === APPROVAL_POLICY.id;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await agentsApi.update(agent.key, { approval_policy_id: policyId });
      setAgent(saved);
      push("Approval policy saved.", "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Approval">
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <p className="muted">Bind a registered policy. Gates listed here cannot be removed from this screen.</p>
        <Field label="Approval policy" error={error ?? undefined}>
          <Select value={policyId} disabled={!agent.writable} onChange={(event) => setPolicyId(event.target.value)}>
            <option value={APPROVAL_POLICY.id}>{APPROVAL_POLICY.label}</option>
            {known ? null : <option value={policyId}>{policyId}</option>}
          </Select>
        </Field>
        <p className="muted">{known ? APPROVAL_POLICY.summary : "This policy id is stored on the agent."}</p>
        {known ? (
          <ul className="stack">
            {APPROVAL_POLICY.gates.map((gate) => (
              <li key={gate}>{gate.replaceAll("_", " ")}</li>
            ))}
          </ul>
        ) : null}
        {agent.writable ? (
          <Button type="submit" disabled={busy}>
            Save approval policy
          </Button>
        ) : null}
      </form>
    </Card>
  );
}
