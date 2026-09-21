import type { TraceStep } from "../../types/api";

export const PUBLISH_STEPS = [
  { tool: "validate_image", label: "Image validated" },
  { tool: "prepare_image", label: "Image prepared" },
  { tool: "upload_image", label: "Uploaded" },
  { tool: "create_instagram_media", label: "Media container created" },
  { tool: "publish_instagram_media", label: "Published" },
  { tool: "verify_publication", label: "Verified" },
] as const;

export const CONTENT_STEPS = [
  { tool: "prompt_received", label: "Prompt received" },
  { tool: "content_strategy", label: "Content strategy created" },
  { tool: "image_generated", label: "Image generated" },
  { tool: "image_saved", label: "Image saved" },
] as const;

function mark(status: string): string {
  if (status === "success") return "✓";
  if (status === "failed") return "✗";
  if (status === "running") return "…";
  if (status === "skipped") return "–";
  return "○";
}

interface Props {
  steps?: TraceStep[] | null;
  currentStep?: string | null;
  labels?: { tool: string; label: string }[];
}

export function Timeline({ steps, currentStep, labels = [...PUBLISH_STEPS] }: Props) {
  const byTool: Record<string, TraceStep> = {};
  for (const step of steps ?? []) {
    byTool[step.tool] = step;
  }

  return (
    <ol className="timeline">
      {labels.map((item) => {
        const recorded = byTool[item.tool];
        let status = recorded?.status ?? "pending";
        if (!recorded && currentStep === item.tool) status = "running";
        return (
          <li key={item.tool} className={status}>
            <span aria-hidden="true">{mark(status)}</span>
            <span>{item.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
