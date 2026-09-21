const TZ = "Asia/Kolkata";

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: TZ,
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatDay(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: TZ,
    dateStyle: "medium",
  }).format(date);
}

function dayKey(date: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function isSameDay(value: string | null | undefined, compare = new Date()): boolean {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  return dayKey(date) === dayKey(compare);
}

export function isSameMonth(value: string | null | undefined, compare = new Date()): boolean {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
  });
  return fmt.format(date) === fmt.format(compare);
}

export function isPublished(status?: string | null): boolean {
  return (status ?? "").toUpperCase() === "PUBLISHED";
}

export function imageSrc(
  url?: string | null,
  filename?: string | null,
  imageId?: string | null,
): string | undefined {
  if (url) return url;
  if (imageId) return `/api/v1/media/generated/${encodeURIComponent(imageId)}`;
  if (filename) return `/api/v1/media/${encodeURIComponent(filename)}`;
  return undefined;
}
