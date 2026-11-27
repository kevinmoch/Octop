import assert from "node:assert/strict";
import { test } from "node:test";

import { FrameBridge } from "./frame-bridge";
import { IPC_CHANNELS } from "../../shared/ipc-contract";
import type { TabWebContents } from "./tabs";

class FakeContents implements TabWebContents {
  static nextId = 1;
  readonly id = FakeContents.nextId++;
  destroyed = false;
  readonly messages: Array<{ channel: string; payload: Record<string, unknown> }> = [];
  fetchResult: unknown = { status: 200, headers: { "content-type": "text/html" }, body: "aGk=" };
  lastScript = "";

  getURL(): string {
    return "";
  }

  getTitle(): string {
    return "";
  }

  readonly navigationHistory = {
    canGoBack: () => false,
    canGoForward: () => false,
    goBack: () => undefined,
    goForward: () => undefined,
  };

  isLoading(): boolean {
    return false;
  }

  isDestroyed(): boolean {
    return this.destroyed;
  }

  async loadURL(): Promise<void> {}

  reload(): void {}

  setZoomLevel(): void {}
  close(): void {}
  setWindowOpenHandler(): { action: "deny" } {
    return { action: "deny" };
  }
  on(): unknown {
    return this;
  }

  send(channel: string, payload: Record<string, unknown>): void {
    this.messages.push({ channel, payload });
  }

  async executeJavaScript(code: string): Promise<unknown> {
    this.lastScript = code;
    return this.fetchResult;
  }
}

function harness(options: { timeoutMs?: number } = {}) {
  const contents = new FakeContents();
  const bridge = new FrameBridge(
    { getWebContents: (tabId) => (tabId === "tab-1" ? contents : undefined) },
    options,
  );
  return { bridge, contents };
}

function lastRequestId(contents: FakeContents): string {
  const message = contents.messages.at(-1);
  assert.ok(message, "expected a frame request");
  assert.equal(message.channel, IPC_CHANNELS.browserFrameRequest);
  const requestId = message.payload.requestId;
  assert.equal(typeof requestId, "string");
  return requestId as string;
}

test("request forwards to the tab and resolves its reply", async () => {
  const { bridge, contents } = harness();
  const pending = bridge.request("tab-1", { type: "perceive" });
  const requestId = lastRequestId(contents);
  bridge.handleResponse(contents.id, { requestId, ok: true, reply: { type: "perceive-result" } });
  assert.deepEqual(await pending, { type: "perceive-result" });
});

test("a reply from another webContents is ignored", async () => {
  const { bridge, contents } = harness();
  const pending = bridge.request("tab-1", { type: "perceive" });
  const requestId = lastRequestId(contents);
  bridge.handleResponse(contents.id + 1, { requestId, ok: true, reply: { spoofed: true } });
  bridge.handleResponse(contents.id, { requestId, ok: true, reply: { real: true } });
  assert.deepEqual(await pending, { real: true });
});

test("a failed reply rejects with the tab's message", async () => {
  const { bridge, contents } = harness();
  const pending = bridge.request("tab-1", { type: "perceive" });
  const requestId = lastRequestId(contents);
  bridge.handleResponse(contents.id, { requestId, ok: false, error: "page went away" });
  await assert.rejects(pending, /page went away/);
});

test("a missing tab is rejected", async () => {
  const { bridge } = harness();
  await assert.rejects(bridge.request("tab-9", { type: "perceive" }), /unavailable/);
});

test("a destroyed tab is rejected", async () => {
  const { bridge, contents } = harness();
  contents.destroyed = true;
  await assert.rejects(bridge.request("tab-1", { type: "perceive" }), /unavailable/);
});

test("requests time out instead of hanging", async () => {
  const { bridge } = harness({ timeoutMs: 5 });
  await assert.rejects(bridge.request("tab-1", { type: "perceive" }), /timed out/);
});

test("dispose rejects in-flight requests", async () => {
  const { bridge } = harness();
  const pending = bridge.request("tab-1", { type: "perceive" });
  bridge.dispose();
  await assert.rejects(pending, /closed/);
});

test("fetchInTab runs a credentialed fetch in the tab", async () => {
  const { bridge, contents } = harness();
  const result = await bridge.fetchInTab("tab-1", "https://api.test/data", {
    method: "post",
    headers: { "x-test": "1" },
    body: '{"a":1}',
  });
  assert.deepEqual(result, {
    status: 200,
    headers: { "content-type": "text/html" },
    body: "aGk=",
  });
  assert.match(contents.lastScript, /credentials: "include"/);
  assert.match(contents.lastScript, /"POST"/);
  assert.match(contents.lastScript, /"x-test":"1"/);
});

test("fetchInTab only accepts http(s) targets", async () => {
  const { bridge } = harness();
  await assert.rejects(bridge.fetchInTab("tab-1", "file:///etc/passwd"), /http/);
  await assert.rejects(bridge.fetchInTab("tab-1", "javascript:alert(1)"), /http/);
});

test("fetchInTab refuses unsupported methods and header shapes", async () => {
  const { bridge } = harness();
  await assert.rejects(bridge.fetchInTab("tab-1", "https://api.test", { method: "TRACE" }), /method/);
  await assert.rejects(
    bridge.fetchInTab("tab-1", "https://api.test", { headers: { "bad header": "x" } }),
    /header name/,
  );
});

test("fetchInTab rejects a malformed tab reply", async () => {
  const { bridge, contents } = harness();
  contents.fetchResult = { status: "200", headers: {}, body: "" };
  await assert.rejects(bridge.fetchInTab("tab-1", "https://api.test"), /invalid fetch result/);
});
