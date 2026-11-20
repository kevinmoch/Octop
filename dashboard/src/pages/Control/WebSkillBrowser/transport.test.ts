// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OctopHostBridge } from "../../../../webskill/src/bridge";
import { OctopPageAgentTransport } from "../../../../webskill/src/transport";

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readonly readyState = FakeWebSocket.OPEN;
  readonly requests: Record<string, unknown>[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onclose?: () => void;
  onerror?: () => void;

  constructor(_url: string) {
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }

  send(raw: string) {
    const request = JSON.parse(raw) as Record<string, unknown>;
    this.requests.push(request);
    if (request.type === "execute") {
      queueMicrotask(
        () =>
          this.onmessage?.({
            data: JSON.stringify({
              type: "execute-result",
              outcome: { ok: true, target: { role: "button", name: "Attach" } },
              requestId: request.requestId,
            }),
          }),
      );
      return;
    }
    const child = request.scope !== undefined && "frames" in request.scope;
    const reply = child
      ? {
          type: "perceive-result",
          result: {
            nodes: [{ role: "button", ref: "frame-ref", name: "Frame action" }],
            page: { url: "https://example.test/frame" },
          },
          targets: [{ ref: "frame-ref", role: "button", name: "Frame action" }],
        }
      : {
          type: "perceive-result",
          result: {
            nodes: [{ role: "button", ref: "main-ref", name: "Main action" }],
            page: { url: "https://example.test/" },
            nestedFrames: [
              {
                url: "https://example.test/frame",
                hint: "same-frame",
                frame: "iframe#same-frame",
              },
            ],
          },
          targets: [{ ref: "main-ref", role: "button", name: "Main action" }],
          document: { key: "doc-1", url: "https://example.test/" },
        };
    queueMicrotask(
      () =>
        this.onmessage?.({
          data: JSON.stringify({ ...reply, requestId: request.requestId }),
        }),
    );
  }

  close() {
    this.onclose?.();
  }
}

describe("OctopPageAgentTransport frame routing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("drills into a discovered frame and aggregates through the SDK router", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    const bridge = {
      call: vi.fn(async () => ({ ticket: "test-ticket" })),
    } as unknown as OctopHostBridge;
    const transport = new OctopPageAgentTransport(bridge);

    const reply = await transport.send({
      type: "perceive",
      scope: { include: ["body"] },
    });

    expect(reply.type).toBe("perceive-result");
    if (reply.type !== "perceive-result") return;
    expect(reply.result.nodes).toHaveLength(2);
    expect(reply.targets).toHaveLength(2);
    expect(reply.targets[1]).toMatchObject({
      ref: "frame-ref",
      frame: "same-frame (example.test)",
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
    const requests = FakeWebSocket.instances[0]!.requests;
    expect(requests[0]!.actionScope).toEqual({ include: ["body"] });
    expect(requests).toHaveLength(2);
    expect(requests[1]!.actionScope).toEqual({ include: ["body"] });
    expect(requests[1]!.scope).toEqual({
      frames: [{ frame: ["iframe#same-frame"], include: ["body"] }],
    });
    transport.dispose();
  });

  it("asks the host for files before an attach action", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    const calls: string[] = [];
    const bridge = {
      call: vi.fn(async (api: string) => {
        calls.push(api);
        if (api === "attach-files") return { count: 1 };
        if (api === "grant") return { grant: "test-grant" };
        return { ticket: "test-ticket" };
      }),
    } as unknown as OctopHostBridge;
    const transport = new OctopPageAgentTransport(bridge);

    const reply = await transport.send({
      type: "execute",
      request: { ref: "ws_ref", action: "attach" },
    });

    expect(reply.type).toBe("execute-result");
    expect(calls).toContain("attach-files");
    expect(calls.indexOf("attach-files")).toBeLessThan(calls.indexOf("grant"));
    transport.dispose();
  });

  it("rejects attach when the user cancels file selection", async () => {
    const bridge = {
      call: vi.fn(async (api: string) =>
        api === "attach-files"
          ? { cancelled: true }
          : { ticket: "test-ticket" },
      ),
    } as unknown as OctopHostBridge;
    const transport = new OctopPageAgentTransport(bridge);
    await expect(
      transport.send({
        type: "execute",
        request: { ref: "ws_ref", action: "attach" },
      }),
    ).rejects.toThrow("no files were selected");
    transport.dispose();
  });

  it("preserves the public write-lock error code", async () => {
    class ErrorWebSocket extends FakeWebSocket {
      send(raw: string) {
        const request = JSON.parse(raw) as Record<string, unknown>;
        queueMicrotask(
          () =>
            this.onmessage?.({
              data: JSON.stringify({
                type: "error",
                code: "WRITE_LOCKED",
                message: "Another writer holds the browser lease",
                requestId: request.requestId,
              }),
            }),
        );
      }
    }
    vi.stubGlobal("WebSocket", ErrorWebSocket);
    const bridge = {
      call: vi.fn(async () => ({ ticket: "test-ticket" })),
    } as unknown as OctopHostBridge;
    const transport = new OctopPageAgentTransport(bridge);

    const reply = await transport.send({
      type: "perceive",
      scope: { include: ["body"] },
    });

    expect(reply).toMatchObject({ type: "error", code: "WRITE_LOCKED" });
    transport.dispose();
  });
});
