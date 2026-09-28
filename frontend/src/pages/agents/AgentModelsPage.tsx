import { useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Field, Input, Select } from "../../components/ui/Field";
import { useToast } from "../../context/ToastContext";
import { IMAGE_PROVIDERS, LLM_PROVIDERS, VISION_PROVIDERS, currentVersion } from "../../lib/agentCatalog";
import { agentsApi, userMessageFor } from "../../services/api";
import type { ModelBinding, ModelRole } from "../../types/agents";
import { useAgentConfig } from "./AgentLayout";

const ROLES: { role: ModelRole; label: string; modelLabel: string; providers: { id: string; label: string }[] }[] = [
  { role: "content_planning", label: "LLM provider", modelLabel: "LLM model", providers: LLM_PROVIDERS },
  { role: "image_generation", label: "Image provider", modelLabel: "Image model", providers: IMAGE_PROVIDERS },
  { role: "vision_qa", label: "Vision provider", modelLabel: "Vision model", providers: VISION_PROVIDERS },
];

function bindingsOf(models: ModelBinding[]): Record<ModelRole, ModelBinding> {
  const fallback: Record<ModelRole, ModelBinding> = {
    content_planning: { role: "content_planning", implementation: "deepseek", model: "" },
    image_generation: { role: "image_generation", implementation: "openai", model: "" },
    vision_qa: { role: "vision_qa", implementation: "deepseek", model: "" },
  };
  for (const item of models) {
    if (item.role in fallback) fallback[item.role] = item;
  }
  return fallback;
}

export function AgentModelsPage() {
  const { agent, setAgent } = useAgentConfig();
  const { push } = useToast();
  const version = currentVersion(agent);
  const [form, setForm] = useState(() => bindingsOf(version?.models ?? []));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function update(role: ModelRole, patch: Partial<ModelBinding>) {
    setForm((current) => ({ ...current, [role]: { ...current[role], ...patch } }));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await agentsApi.update(agent.key, {
        models: ROLES.map((item) => form[item.role]),
      });
      setAgent(saved);
      push("Models saved.", "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Models">
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <p className="muted">Choose a provider and model name. This screen has no field for an API key.</p>
        {error ? <p className="error-box">{error}</p> : null}
        {ROLES.map((item) => (
          <div className="grid two" key={item.role}>
            <Field label={item.label}>
              <Select
                value={form[item.role].implementation}
                disabled={!agent.writable}
                onChange={(event) => update(item.role, { implementation: event.target.value })}
              >
                {item.providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={item.modelLabel} hint="Model name only.">
              <Input
                value={form[item.role].model}
                maxLength={128}
                disabled={!agent.writable}
                onChange={(event) => update(item.role, { model: event.target.value })}
              />
            </Field>
          </div>
        ))}
        {agent.writable ? (
          <Button type="submit" disabled={busy}>
            Save models
          </Button>
        ) : null}
      </form>
    </Card>
  );
}
