import { useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field, Select } from "../../components/ui/Field";
import { useToast } from "../../context/ToastContext";
import { ASSET_ROLES, KNOWLEDGE_SOURCES, MCP_TOOLS, TRIGGERS, currentVersion } from "../../lib/agentCatalog";
import { agentsApi, userMessageFor } from "../../services/api";
import { useAgentConfig } from "./AgentLayout";

interface Choice {
  id: string;
  label: string;
}

export function AgentToolsPage() {
  const { agent, setAgent } = useAgentConfig();
  const selected = currentVersion(agent)?.tool_names ?? [];
  return (
    <ChoiceEditor
      title="MCP tools"
      hint="MCP tools are read-only. Instagram publishing tools are not offered here."
      choices={MCP_TOOLS}
      selected={selected}
      writable={agent.writable}
      saveLabel="Save tools"
      onSave={(ids) => agentsApi.update(agent.key, { tool_names: ids }).then(setAgent)}
    />
  );
}

export function AgentAssetsPage() {
  const { agent, setAgent } = useAgentConfig();
  const selected = currentVersion(agent)?.asset_roles ?? [];
  return (
    <ChoiceEditor
      title="Assets"
      hint="Bind asset roles. Files stay in the existing asset library. Paths and tokens are not shown."
      choices={ASSET_ROLES}
      selected={selected}
      writable={agent.writable}
      saveLabel="Save assets"
      onSave={(ids) => agentsApi.update(agent.key, { asset_roles: ids }).then(setAgent)}
    />
  );
}

export function AgentKnowledgePage() {
  const { agent, setAgent } = useAgentConfig();
  const selected = currentVersion(agent)?.knowledge_sources ?? [];
  return (
    <ChoiceEditor
      title="Knowledge"
      hint="Bind named sources the server already loads. This page does not upload a document corpus."
      choices={KNOWLEDGE_SOURCES}
      selected={selected}
      writable={agent.writable}
      saveLabel="Save knowledge"
      onSave={(ids) => agentsApi.update(agent.key, { knowledge_sources: ids }).then(setAgent)}
    />
  );
}

export function AgentTriggersPage() {
  const { agent, setAgent } = useAgentConfig();
  const { push } = useToast();
  const current = currentVersion(agent)?.trigger_key ?? "reference.run";
  const [trigger, setTrigger] = useState(current);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const options = TRIGGERS.some((item) => item.id === trigger) ? TRIGGERS : [{ id: trigger, label: trigger }, ...TRIGGERS];

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setAgent(await agentsApi.update(agent.key, { trigger_key: trigger }));
      push("Triggers saved.", "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Triggers">
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <p className="muted">A trigger starts the registered workflow. It does not add a new publisher.</p>
        <Field label="Trigger" error={error ?? undefined}>
          <Select value={trigger} disabled={!agent.writable} onChange={(event) => setTrigger(event.target.value)}>
            {options.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
        {agent.writable ? (
          <Button type="submit" disabled={busy}>
            Save triggers
          </Button>
        ) : null}
      </form>
    </Card>
  );
}

function ChoiceEditor({
  title,
  hint,
  choices,
  selected,
  writable,
  saveLabel,
  onSave,
}: {
  title: string;
  hint: string;
  choices: Choice[];
  selected: string[];
  writable: boolean;
  saveLabel: string;
  onSave: (ids: string[]) => Promise<unknown>;
}) {
  const { push } = useToast();
  const [ids, setIds] = useState(selected);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function toggle(id: string) {
    setIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSave(ids);
      push(`${title} saved.`, "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={title}>
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <p className="muted">{hint}</p>
        {error ? <p className="error-box">{error}</p> : null}
        <ul className="check-list">
          {choices.map((choice) => (
            <li key={choice.id}>
              <label>
                <input
                  type="checkbox"
                  checked={ids.includes(choice.id)}
                  disabled={!writable}
                  onChange={() => toggle(choice.id)}
                />
                <span>{choice.label}</span>
              </label>
            </li>
          ))}
        </ul>
        {writable ? (
          <Button type="submit" disabled={busy}>
            {saveLabel}
          </Button>
        ) : null}
      </form>
    </Card>
  );
}
