import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field, Input, Textarea } from "../../components/ui/Field";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useToast } from "../../context/ToastContext";
import { agentsApi, userMessageFor } from "../../services/api";
import { useAgentConfig } from "./AgentLayout";

export function AgentOverviewPage() {
  const { agent, setAgent } = useAgentConfig();
  const { push } = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState(agent.display_name);
  const [description, setDescription] = useState(agent.description);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await agentsApi.update(agent.key, {
        display_name: name.trim(),
        description: description.trim(),
      });
      setAgent(saved);
      push("Agent saved.", "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  async function pause() {
    setBusy(true);
    try {
      setAgent(await agentsApi.disable(agent.key));
      push("Agent paused.", "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  async function retire() {
    setBusy(true);
    try {
      await agentsApi.retire(agent.key);
      push("Agent retired.", "success");
      navigate("/agents");
    } catch (err) {
      setError(userMessageFor(err));
      setBusy(false);
    }
  }

  return (
    <Card title="Profile">
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <p className="row">
          <StatusBadge value={agent.status} />
          <span className="faint">{agent.owner === "system" ? "System" : agent.key}</span>
        </p>
        <Field label="Agent name" error={error ?? undefined}>
          <Input
            value={name}
            maxLength={120}
            disabled={!agent.writable}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="Description">
          <Textarea
            value={description}
            maxLength={500}
            rows={4}
            disabled={!agent.writable}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        {agent.writable ? (
          <div className="row">
            <Button type="submit" disabled={busy || !name.trim()}>
              Save profile
            </Button>
            <Button type="button" variant="secondary" disabled={busy || agent.status === "paused"} onClick={() => void pause()}>
              Pause
            </Button>
            <Button type="button" variant="danger" disabled={busy} onClick={() => void retire()}>
              Retire
            </Button>
          </div>
        ) : (
          <p>
            <Link to="/instagram">Open Instagram studio</Link>
          </p>
        )}
      </form>
    </Card>
  );
}
