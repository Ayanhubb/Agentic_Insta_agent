import type { TaskStatusPayload } from "../../types/api";
import { request } from "./client";
import { API } from "./endpoints";

export const tasksApi = {
  get(taskId: string): Promise<TaskStatusPayload> {
    return request<TaskStatusPayload>(API.tasks.one(taskId));
  },
};

export function subscribeTaskEvents(
  taskId: string,
  onEvent: (payload: TaskStatusPayload) => void,
  onError?: (error: unknown) => void,
): () => void {
  const url = API.tasks.events(taskId);
  let closed = false;
  let source: EventSource | null = null;
  let pollTimer: number | null = null;

  const stopPoll = () => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const terminal = (status: string | undefined) =>
    status === "completed" || status === "failed";

  const startPoll = () => {
    if (pollTimer !== null) return;
    const tick = async () => {
      try {
        const payload = await tasksApi.get(taskId);
        if (closed) return;
        onEvent(payload);
        if (terminal(payload.status)) {
          stopPoll();
        }
      } catch (error) {
        if (!closed) onError?.(error);
      }
    };
    void tick();
    pollTimer = window.setInterval(() => void tick(), 1500);
  };

  try {
    source = new EventSource(url);
    source.addEventListener("status", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as TaskStatusPayload;
        onEvent(payload);
        if (terminal(payload.status)) {
          source?.close();
        }
      } catch (error) {
        onError?.(error);
      }
    });
    source.onerror = () => {
      source?.close();
      source = null;
      if (!closed) startPoll();
    };
  } catch {
    startPoll();
  }

  return () => {
    closed = true;
    source?.close();
    stopPoll();
  };
}
