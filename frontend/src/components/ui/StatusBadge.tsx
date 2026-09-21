const TONE: Record<string, string> = {
  PUBLISHED: "ok",
  completed: "ok",
  success: "ok",
  APPROVED: "accent",
  GENERATED: "info",
  PUBLISHING: "warn",
  publishing: "warn",
  FAILED: "danger",
  failed: "danger",
  AMBIGUOUS_PUBLICATION: "warn",
  connected: "ok",
  disconnected: "warn",
};

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="badge">Unknown</span>;
  const tone = TONE[value] ?? "info";
  return <span className={`badge ${tone}`}>{value.replaceAll("_", " ")}</span>;
}
