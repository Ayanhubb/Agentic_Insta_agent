import { useEffect, useRef, useState, type FormEvent } from "react";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Button } from "../components/ui/Button";
import { Field, Textarea } from "../components/ui/Field";
import { ErrorState } from "../components/ui/States";
import { useToast } from "../context/ToastContext";
import { imageSrc } from "../lib/format";
import { generationApi, isNotImplemented, subscribeTaskEvents, userMessageFor, ApiError } from "../services/api";
import type { GeneratedImage, TaskStatusPayload } from "../types/api";
import { Timeline, CONTENT_STEPS, PUBLISH_STEPS } from "../components/ui/Timeline";

export function GeneratePage() {
  const { push } = useToast();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [image, setImage] = useState<GeneratedImage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [task, setTask] = useState<TaskStatusPayload | null>(null);
  const stopEvents = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      stopEvents.current?.();
    };
  }, []);

  function generationError(err: unknown): string {
    if (isNotImplemented(err) || (err instanceof ApiError && err.status === 404)) {
      return "The content generation API is not available.";
    }
    return userMessageFor(err);
  }

  async function generate(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await generationApi.create(prompt);
      setImage(created);
      setTask(null);
      push("Image generated. Approve it to publish.", "success");
    } catch (err) {
      setError(generationError(err));
    } finally {
      setBusy(false);
    }
  }

  async function regenerate() {
    if (!image) {
      await generate();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await generationApi.regenerate(image.id);
      setImage(next);
      setTask(null);
      push("Image regenerated. Approve it to publish.", "success");
    } catch (err) {
      setError(generationError(err));
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!image) return;
    setBusy(true);
    setError(null);
    try {
      const result = await generationApi.approve(image.id);
      setImage(result.image);
      setConfirmOpen(false);
      push("Publishing started through the Instagram Agent.", "success");
      if (result.taskId) {
        stopEvents.current?.();
        stopEvents.current = subscribeTaskEvents(result.taskId, setTask);
      }
    } catch (err) {
      setError(generationError(err));
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!image) return;
    setBusy(true);
    try {
      await generationApi.reject(image.id);
      setImage(null);
      push("Image rejected.", "info");
    } catch (err) {
      setError(generationError(err));
    } finally {
      setBusy(false);
    }
  }

  const preview = imageSrc(image?.preview_url || image?.image_url, image?.filename, image?.id);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>AI Generator</h1>
          <p className="lede">
            Generate a preview, then approve it. Manual posts never publish without approval.
          </p>
        </div>
      </div>
      <div className="grid two">
        <form className="card stack" onSubmit={(event) => void generate(event)}>
          <Field label="Prompt">
            <Textarea
              required
              rows={8}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
          </Field>
          {error ? <ErrorState message={error} /> : null}
          <Button type="submit" disabled={busy}>
            {busy ? "Generating…" : "Generate"}
          </Button>
        </form>
        <article className="card">
          {image ? (
            <div className="stack">
              <div className="preview-frame">
                {preview ? <img src={preview} alt="Generated preview" /> : <p>Preview unavailable</p>}
              </div>
              <p className="muted">Original prompt</p>
              <p>{image.original_prompt}</p>
              <p className="muted">Enhanced prompt</p>
              <p>{image.enhanced_prompt}</p>
              <div className="row">
                <Button type="button" variant="secondary" disabled={busy} onClick={() => void regenerate()}>
                  Regenerate
                </Button>
                <Button type="button" variant="ghost" disabled={busy} onClick={() => void reject()}>
                  Reject
                </Button>
                <Button type="button" disabled={busy} onClick={() => setConfirmOpen(true)}>
                  Approve & Post
                </Button>
              </div>
            </div>
          ) : (
            <p className="muted">Generate an image to preview it here. The Content Agent never publishes.</p>
          )}
        </article>
      </div>
      <article className="card" style={{ marginTop: 16 }}>
        <h2>Agent activity</h2>
        <Timeline
          steps={task?.steps ?? task?.execution_trace}
          currentStep={task?.current_step}
          labels={[...CONTENT_STEPS, ...PUBLISH_STEPS]}
        />
      </article>
      {confirmOpen ? (
        <ConfirmDialog
          title="Publish this image?"
          body="This sends the approved image to the Instagram Agent. It will not publish again if verification is still pending."
          confirmLabel="Approve & Post"
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void approve()}
        />
      ) : null}
    </div>
  );
}
