import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup, configure } from "@testing-library/react";

configure({ asyncUtilTimeout: 5000 });

class MemoryEventSource {
  url: string;
  onerror: ((event: Event) => void) | null = null;
  private listeners: Record<string, Array<(event: MessageEvent) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MemoryEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners[type] = this.listeners[type] ?? [];
    this.listeners[type].push(listener);
  }

  emit(type: string, data: unknown) {
    const event = { data: JSON.stringify(data) } as MessageEvent;
    for (const listener of this.listeners[type] ?? []) listener(event);
  }

  close() {
    MemoryEventSource.instances = MemoryEventSource.instances.filter((item) => item !== this);
  }

  static instances: MemoryEventSource[] = [];
}

beforeEach(() => {
  vi.stubGlobal("EventSource", MemoryEventSource);
});

afterEach(() => {
  cleanup();
  MemoryEventSource.instances = [];
  window.localStorage.clear();
});
