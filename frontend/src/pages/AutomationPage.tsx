import { useEffect, useState } from "react";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Field, Input } from "../components/ui/Field";
import { ErrorState, LoadingState } from "../components/ui/States";
import { useToast } from "../context/ToastContext";
import { automationApi, userMessageFor } from "../services/api";
import type { AutomationSettings } from "../types/api";

type ConfirmKind = "daily" | "festival" | null;

export function AutomationPage() {
  const { push } = useToast();
  const [form, setForm] = useState<AutomationSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmKind, setConfirmKind] = useState<ConfirmKind>(null);

  useEffect(() => {
    automationApi
      .get()
      .then(setForm)
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!form) return <LoadingState label="Loading automation…" />;

  async function save(next: AutomationSettings) {
    setBusy(true);
    try {
      const saved = await automationApi.update(next);
      setForm(saved);
      push("Automation saved.", "success");
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
          <h1>Automation</h1>
          <p className="lede">Daily slots are idempotent: one successful post per Asia/Kolkata day.</p>
        </div>
      </div>
      <form
        className="card stack"
        onSubmit={(event) => {
          event.preventDefault();
          void save(form);
        }}
      >
        <Field label="Daily automation">
          <input
            type="checkbox"
            checked={Boolean(form.daily_enabled)}
            onChange={(event) => setForm({ ...form, daily_enabled: event.target.checked })}
          />
        </Field>
        <Field
          label="Daily posts per day"
          hint="The API stores 1 successful post per Asia/Kolkata day."
        >
          <Input
            type="number"
            min={1}
            max={1}
            value={form.daily_posts_per_day ?? 1}
            onChange={(event) =>
              setForm({ ...form, daily_posts_per_day: Number(event.target.value) })
            }
          />
        </Field>
        <Field label="Posting time">
          <Input
            type="time"
            value={form.daily_post_time || "10:00"}
            onChange={(event) => setForm({ ...form, daily_post_time: event.target.value })}
          />
        </Field>
          <Field label="Auto publish">
            <input
              type="checkbox"
              aria-label="Auto publish"
              checked={Boolean(form.auto_daily_publish ?? form.auto_publish)}
            onChange={(event) => {
              if (event.target.checked && !(form.auto_daily_publish ?? form.auto_publish)) {
                setConfirmKind("daily");
                return;
              }
              setForm({ ...form, auto_daily_publish: event.target.checked, auto_publish: event.target.checked });
            }}
          />
        </Field>
        <Field label="Festival automation">
          <input
            type="checkbox"
            checked={Boolean(form.festival_enabled)}
            onChange={(event) => setForm({ ...form, festival_enabled: event.target.checked })}
          />
        </Field>
        <Field label="Posts per festival" hint="Minimum 2">
          <Input
            type="number"
            min={2}
            value={form.festival_posts_per_festival ?? 2}
            onChange={(event) =>
              setForm({ ...form, festival_posts_per_festival: Number(event.target.value) })
            }
          />
        </Field>
        <Field label="Auto publish festival posts">
          <input
            type="checkbox"
            checked={Boolean(form.auto_festival_publish)}
            onChange={(event) => {
              if (event.target.checked && !form.auto_festival_publish) {
                setConfirmKind("festival");
                return;
              }
              setForm({ ...form, auto_festival_publish: event.target.checked });
            }}
          />
        </Field>
        <Button type="submit" disabled={busy}>
          Save
        </Button>
      </form>
      {confirmKind ? (
        <ConfirmDialog
          title="Enable automatic publication?"
          body={
            confirmKind === "festival"
              ? "Festival automation will approve and send posts to the Instagram Agent without a manual click."
              : "Daily automation will approve and send posts to the Instagram Agent without a manual click."
          }
          confirmLabel="Enable"
          onCancel={() => setConfirmKind(null)}
          onConfirm={() => {
            if (confirmKind === "festival") {
              setForm({ ...form, auto_festival_publish: true });
            } else {
              setForm({ ...form, auto_daily_publish: true, auto_publish: true });
            }
            setConfirmKind(null);
          }}
        />
      ) : null}
    </div>
  );
}
