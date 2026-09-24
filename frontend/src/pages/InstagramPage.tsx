import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Button } from "../components/ui/Button";
import { Field, Input, Textarea } from "../components/ui/Field";
import { ErrorState, LoadingState } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useToast } from "../context/ToastContext";
import { instagramApi, subscribeTaskEvents, userMessageFor } from "../services/api";
import type { InstagramStatus, TaskStatusPayload } from "../types/api";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Timeline, PUBLISH_STEPS } from "../components/ui/Timeline";

const ACCEPTED_TYPES = new Set(["image/jpeg", "image/jpg", "image/png"]);
const CAPTION_MAX = 2200;

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function isAcceptedFile(file: File): boolean {
  if (ACCEPTED_TYPES.has(file.type.toLowerCase())) return true;
  return /\.(jpe?g|png)$/i.test(file.name);
}

export function InstagramPage() {
  const { push } = useToast();
  const [status, setStatus] = useState<InstagramStatus | null>(null);
  const [accountId, setAccountId] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [caption, setCaption] = useState("");
  const [dragging, setDragging] = useState(false);
  const [task, setTask] = useState<TaskStatusPayload | null>(null);
  const [confirmPost, setConfirmPost] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
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

  useEffect(() => {
    return () => {
      if (preview && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(preview);
    };
  }, [preview]);

  if (error) return <ErrorState message={error} />;
  if (!status) return <LoadingState label="Loading Instagram status…" />;

  const readyToPost = Boolean(status.connected || status.environment_configured);
  const canSubmit = Boolean(file && caption.trim() && !busy);

  function chooseFile(next: File | null) {
    if (preview && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(preview);
    if (!next) {
      setFile(null);
      setPreview(null);
      return;
    }
    if (!isAcceptedFile(next)) {
      push("Choose a JPEG or PNG image.", "error");
      return;
    }
    setFile(next);
    const url = typeof URL.createObjectURL === "function" ? URL.createObjectURL(next) : null;
    setPreview(url);
    setTask(null);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0] ?? null;
    chooseFile(dropped);
  }

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
      const next = await instagramApi.status();
      setStatus(next);
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
    if (!caption.trim()) {
      push("Add a caption before posting.", "error");
      return;
    }
    setConfirmPost(false);
    setBusy(true);
    setTask(null);
    try {
      const started = await instagramApi.publish(file, false, caption);
      push(started.message || "Publishing started.", "success");
      stopEvents.current?.();
      stopEvents.current = subscribeTaskEvents(started.task_id, setTask);
    } catch (err) {
      push(userMessageFor(err), "error");
    } finally {
      setBusy(false);
    }
  }

  const captionPreview = caption.trim().length > 80 ? `${caption.trim().slice(0, 80)}…` : caption.trim();

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Instagram</h1>
          <p className="lede">
            Drop an existing JPEG or PNG, write the caption, then the Instagram Agent posts it.
            This path does not generate images.
          </p>
        </div>
      </div>

      <div className="grid two">
        <section className="card stack">
          <h2>New post</h2>
          <input
            ref={fileInput}
            id="instagram-image"
            className="sr-only"
            type="file"
            accept="image/jpeg,image/png,.jpg,.jpeg,.png"
            onChange={(event) => {
              chooseFile(event.target.files?.[0] ?? null);
              event.target.value = "";
            }}
          />
          <label
            htmlFor="instagram-image"
            className={`dropzone${dragging ? " dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            {preview ? (
              <div className="dropzone-preview">
                <img src={preview} alt={file ? `Preview of ${file.name}` : "Selected image preview"} />
              </div>
            ) : (
              <div className="dropzone-copy">
                <strong>Drop a JPEG or PNG here</strong>
                <span className="muted">or click to browse. The agent will pick up this file.</span>
              </div>
            )}
          </label>
          {file ? (
            <div className="row" style={{ justifyContent: "space-between" }}>
              <p className="muted">
                {file.name} · {formatBytes(file.size)}
              </p>
              <Button type="button" variant="ghost" onClick={() => chooseFile(null)}>
                Remove
              </Button>
            </div>
          ) : null}

          <Field
            label="Caption"
            hint={`${caption.length} / ${CAPTION_MAX} characters. Posted with the image.`}
          >
            <Textarea
              required
              rows={6}
              maxLength={CAPTION_MAX}
              value={caption}
              aria-label="Caption"
              placeholder="Write the caption that should appear on Instagram"
              onChange={(event) => setCaption(event.target.value)}
            />
          </Field>

          {!readyToPost ? (
            <p className="error-box">Connect Instagram or set studio credentials before posting.</p>
          ) : null}

          <Button type="button" disabled={!canSubmit || !readyToPost} onClick={() => setConfirmPost(true)}>
            {busy ? "Posting…" : "Post to Instagram"}
          </Button>
        </section>

        <section className="card stack">
          <h2>Connection</h2>
          <p>
            <StatusBadge
              value={readyToPost ? "connected" : status.status || "disconnected"}
            />{" "}
            {status.connected
              ? status.username || status.instagram_account_id || "Connected"
              : status.environment_configured
                ? "Studio credentials on the server"
                : "Not connected"}
          </p>
          <p className="faint">
            Access tokens stay encrypted on the backend. They never appear in this app.
            {status.environment_configured && !status.connected
              ? " You can post with the studio Instagram credentials already configured on the server."
              : ""}
          </p>
          <details className="connect-details">
            <summary>Connect a different account</summary>
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
          </details>

          <h2>Agent activity</h2>
          <p className="faint">Progress streams while the Instagram Agent prepares and publishes the image.</p>
          <Timeline steps={task?.steps ?? task?.execution_trace} currentStep={task?.current_step} labels={[...PUBLISH_STEPS]} />
          {task?.instagram_media_id ? <p>Instagram media ID: {task.instagram_media_id}</p> : null}
          {task?.permalink ? (
            <p>
              <a href={task.permalink} target="_blank" rel="noreferrer">
                Open on Instagram
              </a>
            </p>
          ) : null}
          {task?.error ? <div className="error-box">{task.error.message}</div> : null}
        </section>
      </div>

      {confirmPost ? (
        <ConfirmDialog
          title="Post this image to Instagram?"
          body={
            captionPreview
              ? `The Instagram Agent will upload the selected file with this caption: “${captionPreview}”`
              : "The Instagram Agent will upload the selected file."
          }
          confirmLabel="Post to Instagram"
          onCancel={() => setConfirmPost(false)}
          onConfirm={() => void publish()}
        />
      ) : null}
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
