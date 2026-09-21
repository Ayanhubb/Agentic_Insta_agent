import { ApiError } from "./errors";
import { getAccessToken, clearAccessToken } from "./session";

const AUTH_EVENT = "auth:unauthorized";

export function onUnauthorized(handler: () => void): () => void {
  const listener = () => handler();
  window.addEventListener(AUTH_EVENT, listener);
  return () => window.removeEventListener(AUTH_EVENT, listener);
}

function apiBase(): string {
  const configured = import.meta.env.VITE_API_BASE;
  return configured && configured.length > 0 ? configured.replace(/\/$/, "") : "";
}

function extractMessage(payload: unknown, fallback: string): { message: string; code?: string } {
  if (!payload || typeof payload !== "object") return { message: fallback };
  const data = payload as Record<string, unknown>;
  const error = data.error;
  if (error && typeof error === "object") {
    const body = error as Record<string, unknown>;
    const message = typeof body.message === "string" ? body.message : fallback;
    const code = typeof body.code === "string" ? body.code : undefined;
    return { message, code };
  }
  if (typeof data.message === "string") return { message: data.message };
  if (typeof data.detail === "string") return { message: data.detail };
  if (Array.isArray(data.detail)) {
    const parts = data.detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean);
    if (parts.length) return { message: parts.join(" ") };
  }
  return { message: fallback };
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function request<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const { timeoutMs = 120_000, signal, headers: initHeaders, ...rest } = init;
  const headers = new Headers(initHeaders);
  const token = getAccessToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  const isFormData = typeof FormData !== "undefined" && rest.body instanceof FormData;
  if (rest.body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  if (signal) {
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  try {
    response = await fetch(`${apiBase()}${path}`, {
      ...rest,
      headers,
      credentials: "include",
      signal: controller.signal,
    });
  } catch {
    throw new ApiError("Could not reach the API. Confirm the FastAPI server is running.", {
      status: 0,
      code: "NETWORK_ERROR",
    });
  } finally {
    window.clearTimeout(timer);
  }

  const payload = await readBody(response);
  const requestId = response.headers.get("X-Request-ID");

  if (!response.ok) {
    const { message, code } = extractMessage(
      payload,
      response.status === 401
        ? "Authentication required."
        : response.status === 403
          ? "You do not have permission to do that."
          : response.status === 404
            ? "Not found."
            : response.status === 422
              ? "The request could not be validated."
              : response.status >= 500
                ? "The server ran into a problem."
                : `Request failed (${response.status}).`,
    );

    if (response.status === 401) {
      clearAccessToken();
      window.dispatchEvent(new Event(AUTH_EVENT));
    }

    throw new ApiError(message, {
      status: response.status,
      code,
      requestId,
      details: payload,
    });
  }

  return payload as T;
}

export function unwrapList<T>(payload: unknown, keys: string[] = ["items", "data", "results"]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of keys) {
      if (Array.isArray(record[key])) return record[key] as T[];
    }
  }
  return [];
}

export function unwrapObject<T>(payload: unknown, keys: string[] = ["data", "item"]): T {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of keys) {
      if (record[key] && typeof record[key] === "object") return record[key] as T;
    }
  }
  return payload as T;
}
