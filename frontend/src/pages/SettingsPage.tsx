import { useEffect, useState } from "react";
import { ChangePasswordForm } from "../components/ChangePasswordForm";
import { ErrorState, LoadingState } from "../components/ui/States";
import { useAuth } from "../context/AuthContext";
import { isNotImplemented, settingsApi, userMessageFor } from "../services/api";
import type { AutomationSettings } from "../types/api";

export function SettingsPage() {
  const { user } = useAuth();
  const [automation, setAutomation] = useState<AutomationSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    settingsApi
      .get()
      .then((payload) => {
        setAutomation(payload.automation ?? null);
      })
      .catch((err) => {
        if (!isNotImplemented(err)) setError(userMessageFor(err));
      })
      .finally(() => setReady(true));
  }, []);

  if (!ready && !error) return <LoadingState label="Loading settings…" />;
  if (error) return <ErrorState message={error} />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p className="lede">{user?.email}</p>
        </div>
      </div>
      <div className="grid two">
        <div className="card">
          <h2>Password</h2>
          <ChangePasswordForm />
        </div>
        <div className="card stack">
          <h2>Workspace</h2>
          <p className="muted">Timezone: {automation?.timezone || "Asia/Kolkata"}</p>
          <p className="muted">Daily automation: {automation?.daily_enabled ? "On" : "Off"}</p>
          <p className="muted">Festival automation: {automation?.festival_enabled ? "On" : "Off"}</p>
          <p className="faint">Secrets and API keys are never returned to this app.</p>
        </div>
      </div>
    </div>
  );
}
