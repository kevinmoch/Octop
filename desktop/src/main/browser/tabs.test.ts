import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BrowserTabManager,
  externalAppUrl,
  normalizeTabUrl,
  scaleBoundsForZoom,
  type BrowserTabEvent,
  type TabBounds,
  type TabView,
  type TabWebContents,
} from "./tabs";

class FakeContents implements TabWebContents {
  static nextId = 1;
  readonly id = FakeContents.nextId++;
  url = "";
  title = "";
  destroyed = false;
  readonly zoomLevels: number[] = [];
  windowOpenHandler?: (details: { url: string }) => { action: "deny" };
  readonly listeners = new Map<string, Array<() => void>>();
  readonly messages: Array<{ channel: string; payload: unknown }> = [];
  loadShouldFail = false;

  getURL(): string {
    return this.url;
  }

  getTitle(): string {
    return this.title;
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

  async loadURL(url: string): Promise<void> {
    if (this.loadShouldFail) throw new Error("ERR_ABORTED");
    this.url = url;
    this.emit("did-start-loading");
    this.emit("did-navigate");
    this.emit("did-stop-loading");
  }

  reload(): void {}

  setZoomLevel(level: number): void {
    this.zoomLevels.push(level);
  }

  close(): void {
    this.destroyed = true;
    this.emit("destroyed");
  }

  setWindowOpenHandler(handler: (details: { url: string }) => { action: "deny" }): void {
    this.windowOpenHandler = handler;
  }

  on(event: string, listener: (...args: never[]) => void): this {
    const listeners = this.listeners.get(event) ?? [];
    listeners.push(listener as () => void);
    this.listeners.set(event, listeners);
    return this;
  }

  send(channel: string, payload: unknown): void {
    this.messages.push({ channel, payload });
  }

  async executeJavaScript(): Promise<unknown> {
    return undefined;
  }

  emit(event: string): void {
    for (const listener of [...(this.listeners.get(event) ?? [])]) listener();
  }
}

class FakeView implements TabView {
  readonly webContents = new FakeContents();
  bounds?: TabBounds;
  visible = false;

  setBounds(bounds: TabBounds): void {
    this.bounds = { ...bounds };
  }

  setVisible(visible: boolean): void {
    this.visible = visible;
  }
}

function harness(options: { maxTabs?: number; onExternalUrl?: (url: string) => void } = {}) {
  const views: FakeView[] = [];
  const added: FakeView[] = [];
  const removed: FakeView[] = [];
  const events: BrowserTabEvent[] = [];
  const manager = new BrowserTabManager<FakeView>(
    {
      contentView: {
        addChildView: (view) => added.push(view),
        removeChildView: (view) => removed.push(view),
      },
    },
    {
      preloadPath: "browser-tab.js",
      defaultUrl: "https://example.com/",
      ...(options.maxTabs !== undefined ? { maxTabs: options.maxTabs } : {}),
      ...(options.onExternalUrl !== undefined ? { onExternalUrl: options.onExternalUrl } : {}),
      createView: () => {
        const view = new FakeView();
        views.push(view);
        return view;
      },
    },
  );
  manager.onEvent((event) => events.push(event));
  return { manager, views, added, removed, events };
}

test("normalizeTabUrl adds https and refuses non-http schemes", () => {
  assert.equal(normalizeTabUrl("example.com", "https://fallback.test/"), "https://example.com/");
  assert.equal(normalizeTabUrl("  http://a.test/x  ", "https://fallback.test/"), "http://a.test/x");
  assert.equal(normalizeTabUrl("", "https://fallback.test/"), "https://fallback.test/");
  assert.throws(() => normalizeTabUrl("javascript:alert(1)", "https://fallback.test/"));
  assert.throws(() => normalizeTabUrl("file:///etc/passwd", "https://fallback.test/"));
  assert.throws(() => normalizeTabUrl("https://", "https://fallback.test/"));
});

test("externalAppUrl only returns OS-handler schemes", () => {
  assert.equal(externalAppUrl("mailto:a@b.test"), "mailto:a@b.test");
  assert.equal(externalAppUrl("tel:+8613800000000"), "tel:+8613800000000");
  assert.equal(externalAppUrl("myapp://open/thing"), "myapp://open/thing");
  assert.equal(externalAppUrl("https://a.test"), undefined);
  assert.equal(externalAppUrl("http://a.test"), undefined);
  assert.equal(externalAppUrl("javascript:alert(1)"), undefined);
  assert.equal(externalAppUrl("data:text/html,hi"), undefined);
  assert.equal(externalAppUrl("file:///etc/passwd"), undefined);
  assert.equal(externalAppUrl("not a url"), undefined);
});

test("scaleBoundsForZoom converts CSS pixels to DIP at any zoom", () => {
  assert.deepEqual(scaleBoundsForZoom({ x: 100, y: 50, width: 800, height: 600 }, 0.5), {
    x: 50,
    y: 25,
    width: 400,
    height: 300,
  });
  assert.deepEqual(scaleBoundsForZoom({ x: 1, y: 2, width: 3, height: 4 }, 1), {
    x: 1,
    y: 2,
    width: 3,
    height: 4,
  });
  assert.deepEqual(scaleBoundsForZoom({ x: 10, y: 20, width: 30, height: 40 }, 0), {
    x: 10,
    y: 20,
    width: 30,
    height: 40,
  });
});

test("create loads the URL, activates, and lists one active tab", async () => {
  const { manager, views, added } = harness();
  const id = manager.create({ url: "example.com" });
  assert.equal(id, "tab-1");
  assert.equal(views.length, 1);
  assert.equal(added.length, 1);
  // A new tab must not inherit a persisted per-origin page zoom.
  assert.ok(
    (views[0]?.webContents.zoomLevels.length ?? 0) > 0 &&
      views[0]?.webContents.zoomLevels.every((level) => level === 0),
  );
  const [tab] = manager.list();
  assert.equal(tab?.id, "tab-1");
  assert.equal(tab?.active, true);
  assert.equal(manager.activeTabId(), "tab-1");
  await Promise.resolve();
  assert.equal(views[0]?.webContents.getURL(), "https://example.com/");
});

test("an inactive tab is loaded but does not steal activation", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  const second = manager.create({ url: "https://two.test", active: false });
  assert.equal(manager.activeTabId(), "tab-1");
  assert.equal(manager.list()[1]?.active, false);
  assert.equal(views[1]?.visible, false);
  assert.equal(second, "tab-2");
});

test("views are only visible while the host page is visible", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  assert.equal(views[0]?.visible, false);
  manager.setHostVisible(true);
  assert.equal(views[0]?.visible, true);
  manager.create({ url: "https://two.test" });
  assert.equal(views[0]?.visible, false);
  assert.equal(views[1]?.visible, true);
  manager.setHostVisible(false);
  assert.equal(views[1]?.visible, false);
});

test("activating a tab applies its own remembered bounds", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  manager.create({ url: "https://two.test" });
  manager.setViewportBounds("tab-1", { x: 10, y: 20, width: 300, height: 400 });
  manager.setViewportBounds("tab-2", { x: 11, y: 21, width: 301, height: 401 });
  assert.deepEqual(views[1]?.bounds, { x: 11, y: 21, width: 301, height: 401 });
  manager.activate("tab-1");
  assert.deepEqual(views[0]?.bounds, { x: 10, y: 20, width: 300, height: 400 });
  // Bounds are rounded before reaching the native view.
  manager.setViewportBounds("tab-1", { x: 1.4, y: 2.6, width: 30.2, height: 40.8 });
  assert.deepEqual(views[0]?.bounds, { x: 1, y: 3, width: 30, height: 41 });
});

test("window.open becomes a new active tab and is still denied", () => {
  const { manager, views, events } = harness();
  const opener = manager.create({ url: "https://one.test" });
  const response = views[0]?.webContents.windowOpenHandler?.({ url: "https://two.test/page" });
  assert.deepEqual(response, { action: "deny" });
  assert.equal(views.length, 2);
  assert.equal(manager.activeTabId(), "tab-2");
  const opened = events.find((event) => event.kind === "opened-by-page");
  assert.deepEqual(opened, {
    kind: "opened-by-page",
    openerId: opener,
    url: "https://two.test/page",
    tabId: "tab-2",
  });
});

test("window.open with a non-http URL opens nothing", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  const response = views[0]?.webContents.windowOpenHandler?.({ url: "javascript:alert(1)" });
  assert.deepEqual(response, { action: "deny" });
  assert.equal(views.length, 1);
  assert.equal(manager.list().length, 1);
});

test("window.open with an OS scheme goes to the shell, not a tab", () => {
  const external: string[] = [];
  const { manager, views } = harness({ onExternalUrl: (url) => external.push(url) });
  manager.create({ url: "https://one.test" });
  const response = views[0]?.webContents.windowOpenHandler?.({
    url: "mailto:help@example.test",
  });
  assert.deepEqual(response, { action: "deny" });
  assert.deepEqual(external, ["mailto:help@example.test"]);
  assert.equal(manager.list().length, 1);
});

test("dangerous window.open schemes never reach the shell", () => {
  const external: string[] = [];
  const { manager, views } = harness({ onExternalUrl: (url) => external.push(url) });
  manager.create({ url: "https://one.test" });
  views[0]?.webContents.windowOpenHandler?.({ url: "file:///etc/passwd" });
  views[0]?.webContents.windowOpenHandler?.({ url: "javascript:alert(1)" });
  assert.deepEqual(external, []);
  assert.equal(manager.list().length, 1);
});

test("maxTabs is enforced", () => {
  const { manager } = harness({ maxTabs: 2 });
  manager.create({ url: "https://one.test" });
  manager.create({ url: "https://two.test" });
  assert.throws(() => manager.create({ url: "https://three.test" }), /At most 2 browser tabs/);
});

test("closing the active tab activates the next one and removes its view", () => {
  const { manager, views, removed, events } = harness();
  manager.create({ url: "https://one.test" });
  manager.create({ url: "https://two.test" });
  manager.close("tab-2");
  assert.equal(removed.length, 1);
  assert.equal(manager.activeTabId(), "tab-1");
  assert.equal(manager.list().length, 1);
  assert.equal(views[1]?.webContents.destroyed, true);
  assert.equal(events.filter((event) => event.kind === "closed").length, 1);
});

test("closing a background tab keeps the active one", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  manager.create({ url: "https://two.test", active: false });
  manager.close("tab-2");
  assert.equal(manager.activeTabId(), "tab-1");
  assert.equal(views[1]?.webContents.destroyed, true);
});

test("closing every tab leaves the manager empty", () => {
  const { manager } = harness();
  manager.create({ url: "https://one.test" });
  manager.close("tab-1");
  assert.equal(manager.activeTabId(), undefined);
  assert.deepEqual(manager.list(), []);
});

test("navigate rejects an unsupported scheme and keeps the current page", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  assert.throws(() => manager.navigate("tab-1", "data:text/html,hi"));
  assert.equal(views[0]?.webContents.getURL(), "https://one.test/");
  manager.navigate("tab-1", "two.test");
  assert.equal(views[0]?.webContents.getURL(), "https://two.test/");
});

test("a failed load does not reject navigation", async () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  if (views[0]) views[0].webContents.loadShouldFail = true;
  manager.navigate("tab-1", "https://two.test");
  await Promise.resolve();
  assert.equal(manager.list().length, 1);
});

test("tab ownership maps webContents ids back to tab ids", () => {
  const { manager, views } = harness();
  manager.create({ url: "https://one.test" });
  manager.create({ url: "https://two.test" });
  const first = views[0]?.webContents.id ?? -1;
  assert.equal(manager.ownsWebContents(first), true);
  assert.equal(manager.tabIdForWebContents(first), "tab-1");
  assert.equal(manager.ownsWebContents(999_999), false);
});

test("dispose closes every tab without touching the host window", () => {
  const { manager, views, removed } = harness();
  manager.create({ url: "https://one.test" });
  manager.create({ url: "https://two.test" });
  manager.dispose();
  assert.equal(views.every((view) => view.webContents.destroyed), true);
  assert.equal(removed.length, 0);
  assert.equal(manager.list().length, 0);
});
