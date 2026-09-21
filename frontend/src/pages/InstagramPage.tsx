import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Field";
import { ErrorState, LoadingState } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useToast } from "../context/ToastContext";
import { instagramApi, subscribeTaskEvents, userMessageFor } from "../services/api";
import type { InstagramStatus, TaskStatusPayload } from "../types/api";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Timeline } from "../components/ui/Timeline";

export function InstagramPage() {
  const { push } = useToast();
  const [status, setStatus] = useState<InstagramStatus | null>(null);
  const [accountId, setAccountId] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [task, setTask] = useState<TaskStatusPayload | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const stopEvents = useRef<(() => void) | null>(null);

  useEffect(() => {
    instagramApi
      .status()
      .then(setStatus)
      .catch((err) => setError(userMessageFor(err)));
    return () => {
      stopEvents.current?.();
    };
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!status) return <LoadingState label="Loading Instagram status…" />;

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const saved = await instagramApi.connect({
        instagram_account_id: accountId,
        access_token: token,
      });
      setStatus(saved);
      setToken("");
      push("Instagram connected.", "success");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setConfirmDisconnect(false);
    setBusy(true);
    try {
      await instagramApi.disconnect();
      setStatus({ connected: false, status: "disconnected" });
      push("Instagram disconnected.", "info");
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!file) {
      push("Choose a JPEG or PNG first.", "error");
      return;
    }
    setBusy(true);
    setTask(null);
    try {
      const started = await instagramApi.publish(file, false);
      push(started.message || "Publishing started.", "success");
      stopEvents.current?.();
      stopEvents.current = subscribeTaskEvents(started.task_id, setTask);
    } catch (err) {
      push(userMessageFor(err), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Instagram</h1>
          <p className="lede">Tokens are stored encrypted on the backend. They never appear in React bundles.</p>
        </div>
      </div>
      <div className="card stack">
        <p>
          <StatusBadge value={status.connected ? "connected" : "disconnected"} />{" "}
          {status.username || status.instagram_account_id || "Not connected"}
        </p>
        <form className="stack" onSubmit={(event) => void connect(event)}>
          <Field label="Instagram account id">
            <Input value={accountId} onChange={(event) => setAccountId(event.target.value)} required />
          </Field>
          <Field label="Access token">
            <Input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
            />
          </Field>
          <div className="row">
            <Button type="submit" disabled={busy}>
              Connect
            </Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirmDisconnect(true)}>
              Disconnect
            </Button>
          </div>
        </form>
      </div>
      <div className="card stack" style={{ marginTop: 16 }}>
        <h2>Manual publish</h2>
        <p className="faint">Uses POST /api/v1/instagram/publish. Progress streams over SSE with HTTP polling fallback.</p>
        <input
          type="file"
          accept="image/jpeg,image/png,.jpg,.jpeg,.png"
          onChange={(event) => {
            const next = event.target.files?.[0] ?? null;
            setFile(next);
            setPreview(next ? URL.createObjectURL(next) : null);
          }}
        />
        {preview ? <img src={preview} alt="Selected image preview" /> : null}
        <Button type="button" disabled={busy || !file} onClick={() => void publish()}>
          Publish to Instagram
        </Button>
        <Timeline steps={task?.steps ?? task?.execution_trace} currentStep={task?.current_step} />
        {task?.instagram_media_id ? <p>Instagram media ID: {task.instagram_media_id}</p> : null}
        {task?.error ? <div className="error-box">{task.error.message}</div> : null}
      </div>
      {confirmDisconnect ? (
        <ConfirmDialog
          title="Disconnect Instagram?"
          body="This account will stop publishing until you connect again. Tokens are never shown in the browser."
          danger
          confirmLabel="Disconnect"
          onConfirm={() => void disconnect()}
          onCancel={() => setConfirmDisconnect(false)}
        />
      ) : null}
    </div>
  );
}
