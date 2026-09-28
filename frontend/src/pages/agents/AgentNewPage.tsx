import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../../components/ui/Button";
import { Field, Input, Textarea } from "../../components/ui/Field";
import { useToast } from "../../context/ToastContext";
import { slugKey } from "../../lib/agentCatalog";
import { agentsApi, userMessageFor } from "../../services/api";

export function AgentNewPage() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const displayName = name.trim();
    const agentKey = (keyEdited ? key : slugKey(name)).trim();
    if (!displayName || !/^[a-z][a-z0-9_]{0,63}$/.test(agentKey)) {
      setError("Use a name and a key that starts with a letter.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await agentsApi.create({
        key: agentKey,
        display_name: displayName,
        description: description.trim(),
      });
      push("Agent created.", "success");
      navigate(`/agents/${created.key}`);
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>New agent</h1>
          <p className="lede">Name the agent. Provider keys are not part of this form.</p>
        </div>
      </div>
      <form className="card stack" onSubmit={(event) => void onSubmit(event)}>
        <Field label="Agent name" error={error ?? undefined}>
          <Input
            value={name}
            maxLength={120}
            onChange={(event) => {
              setName(event.target.value);
              if (!keyEdited) setKey(slugKey(event.target.value));
            }}
          />
        </Field>
        <Field label="Key" hint="Letters, numbers, and underscores. This becomes the agent id.">
          <Input
            value={key}
            maxLength={64}
            onChange={(event) => {
              setKeyEdited(true);
              setKey(event.target.value);
            }}
          />
        </Field>
        <Field label="Description">
          <Textarea
            value={description}
            maxLength={500}
            rows={4}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <div className="row">
          <Button type="submit" disabled={busy}>
            Create agent
          </Button>
          <Link className="btn ghost" to="/agents">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
