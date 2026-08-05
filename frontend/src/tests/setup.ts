import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  MockEventSource.instances = [];
});

class MockEventSource extends EventTarget {
  static instances: MockEventSource[] = [];

  url: string;
  close = vi.fn();

  constructor(url: string) {
    super();
    this.url = url;
    MockEventSource.instances.push(this);
  }
}

Object.defineProperty(window, "EventSource", {
  value: MockEventSource,
  writable: true,
});
