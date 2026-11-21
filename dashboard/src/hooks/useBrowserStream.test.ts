import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/request", () => ({ getAuthToken: () => "test-token" }));
vi.mock("../utils/desktopWsUrl", () => ({
  resolveWsUrl: () => "ws://test/stream",
}));

import { useBrowserStream } from "./useBrowserStream";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  emit(message: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

const original = globalThis.WebSocket;

beforeEach(() => {
  FakeWebSocket.instances = [];
  globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
});

afterEach(() => {
  globalThis.WebSocket = original;
});

describe("useBrowserStream tab switch queue", () => {
  it("reconnects and replays the tab switch after the first tab snapshot", () => {
    const { result } = renderHook(() => useBrowserStream());
    act(() => {
      result.current.connect(
        "https://initial.test",
        800,
        600,
        { onFrame: () => undefined },
        {},
      );
    });

    const first = FakeWebSocket.instances[0]!;
    act(() => first.open());
    expect(first.sent[0]).toMatchObject({ type: "start" });

    act(() =>
      first.emit({
        type: "tabs",
        tabs: [
          { id: "A", url: "https://a.test", title: "A", active: true },
          { id: "B", url: "https://b.test", title: "B", active: false },
        ],
      }),
    );
    expect(result.current.tabs.map((tab) => tab.id)).toEqual(["A", "B"]);

    // Simulate the stream dropping while the user clicks the second tab.
    act(() => first.close());
    expect(result.current.status).toBe("stopped");
    act(() => result.current.switchTab("B"));

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(result.current.tabs.find((tab) => tab.id === "B")?.active).toBe(
      true,
    );
    const second = FakeWebSocket.instances[1]!;
    act(() => second.open());
    act(() =>
      second.emit({
        type: "tabs",
        tabs: [
          { id: "A", url: "https://a.test", title: "A", active: true },
          { id: "B", url: "https://b.test", title: "B", active: false },
        ],
      }),
    );

    const switchMessage = second.sent.find(
      (message) => message.type === "tab_switch",
    );
    expect(switchMessage).toMatchObject({ type: "tab_switch", tab_id: "B" });
    expect(result.current.tabs.find((tab) => tab.id === "B")?.active).toBe(
      true,
    );
  });
});
