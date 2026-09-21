export function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <div className="empty" role="status">
      <strong>{title}</strong>
      <p className="lede" style={{ margin: "8px auto 0", textAlign: "center" }}>
        {body}
      </p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <p>{message}</p>
      {onRetry ? (
        <p>
          <button className="btn secondary" type="button" onClick={onRetry} style={{ marginTop: 10 }}>
            Try again
          </button>
        </p>
      ) : null}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading-box" role="status" aria-live="polite">
      <div className="skeleton" style={{ width: 180, margin: "0 auto 10px" }} />
      <div className="skeleton" style={{ width: 240, margin: "0 auto" }} />
      <p className="faint" style={{ marginTop: 12 }}>{label}</p>
    </div>
  );
}
