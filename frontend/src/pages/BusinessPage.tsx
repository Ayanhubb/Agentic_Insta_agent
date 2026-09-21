import { useEffect, useState, type FormEvent } from "react";
import { Button } from "../components/ui/Button";
import { Field, Input, Textarea } from "../components/ui/Field";
import { ErrorState, LoadingState } from "../components/ui/States";
import { useToast } from "../context/ToastContext";
import { businessApi, userMessageFor } from "../services/api";
import type { BusinessProfile } from "../types/api";

const FIELDS: Array<{ key: keyof BusinessProfile; label: string; textarea?: boolean }> = [
  { key: "business_name", label: "Business name" },
  { key: "business_type", label: "Business type" },
  { key: "business_category", label: "Category" },
  { key: "description", label: "Description", textarea: true },
  { key: "target_audience", label: "Target audience" },
  { key: "location", label: "Location" },
  { key: "brand_style", label: "Brand style" },
  { key: "preferred_language", label: "Preferred language" },
  { key: "products", label: "Products", textarea: true },
  { key: "services", label: "Services", textarea: true },
];

export function BusinessPage() {
  const { push } = useToast();
  const [form, setForm] = useState<BusinessProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    businessApi
      .get()
      .then((profile) => setForm(profile ?? {}))
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!form) return <LoadingState label="Loading business profile…" />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const snapshot = form;
    if (!snapshot) return;
    setBusy(true);
    try {
      const saved = await businessApi.update(snapshot);
      setForm(saved);
      push("Business profile saved.", "success");
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
          <h1>Business</h1>
          <p className="lede">The Content Agent uses this profile for daily and festival strategy.</p>
        </div>
      </div>
      <form className="card stack" onSubmit={(event) => void onSubmit(event)}>
        {FIELDS.map((field) => (
          <Field key={field.key} label={field.label}>
            {field.textarea ? (
              <Textarea
                rows={4}
                value={String(form[field.key] || "")}
                onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
              />
            ) : (
              <Input
                value={String(form[field.key] || "")}
                onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
              />
            )}
          </Field>
        ))}
        <Button type="submit" disabled={busy}>
          Save
        </Button>
      </form>
    </div>
  );
}
