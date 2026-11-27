// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OpenedPageInfo } from "@webskill/sdk/agent";
import {
  localBrowserHost,
  type LocalBrowserHost,
  type LocalBrowserTab,
} from "./localHost";
import { OctopLocalTabWorksetPort, createLocalTabWatcher } from "./pageTabs";
import {
  createLocalDownloadReader,
  createLocalDownloadWatcher,
} from "./localDownloads";
import { createLocalLinkedDocuments } from "./localLinkedDocuments";
import { OctopPageAgentTransport } from "./transport";
import type { OctopHostBridge } from "./bridge";

function localTab(
  id: string,
  url = "https://one.test",
  active = false,
): LocalBrowserTab {
  return {
    id,
    url,
    title: url,
    active,
    canGoBack: false,
    canGoForward: false,
    isLoading: false,
  };
}

function installHost(
  overrides: Partial<LocalBrowserHost> = {},
): LocalBrowserHost {
  let tabs: LocalBrowserTab[] = [localTab("tab-1", "https://one.test", true)];
  const host: LocalBrowserHost = {
    tabs: {
      list: async () => tabs.map((entry) => ({ ...entry })),
      create: vi.fn(
        async ({ url, active = true }: { url: string; active?: boolean }) => {
          const id = `tab-${tabs.length + 1}`;
          tabs = [...tabs, localTab(id, url, active)];
          return id;
        },
      ),
      close: vi.fn(async (id: string) => {
        tabs = tabs.filter((entry) => entry.id !== id);
      }),
      activate: vi.fn(async () => undefined),
      navigate: vi.fn(async () => undefined),
      goBack: vi.fn(async () => undefined),
      goForward: vi.fn(async () => undefined),
      reload: vi.fn(async () => undefined),
      setViewportBounds: vi.fn(),
      setVisible: vi.fn(),
      onChanged: () => () => undefined,
      onEvent: () => () => undefined,
    },
    frame: {
      domRequest: vi.fn(async () => ({
        type: "perceive-result",
        result: { nodes: [] },
        targets: [],
      })),
      fetchInTab: vi.fn(async () => ({
        status: 200,
        headers: { "Content-Type": "text/plain" },
        body: btoa("hello"),
      })),
    },
    downloads: {
      list: vi.fn(async () => []),
      read: vi.fn(async () => ({ contentType: "text/plain", data: "" })),
      openWindow: vi.fn(async () => "0"),
      settleWindow: vi.fn(async () => 1),
    },
  };
  const merged = { ...host, ...overrides } as LocalBrowserHost;
  (window as unknown as { octopDesktop?: unknown }).octopDesktop = {
    browserHost: merged,
  };
  return merged;
}

beforeEach(() => {
  delete (window as unknown as { octopDesktop?: unknown }).octopDesktop;
});

describe("localBrowserHost", () => {
  it("is undefined in server mode", () => {
    expect(localBrowserHost()).toBeUndefined();
  });

  it("reads the shell bridge from the parent window", () => {
    const host = installHost();
    expect(localBrowserHost()).toBe(host);
  });
});

describe("OctopLocalTabWorksetPort", () => {
  it("lists local tabs and moves only the agent focus", async () => {
    const host = installHost();
    const port = new OctopLocalTabWorksetPort(host);
    expect(await port.list()).toEqual([
      { handle: "tab-1", title: "https://one.test", url: "https://one.test" },
    ]);
    await port.focus("tab-2");
    expect(port.focusedHandle()).toBe("tab-2");
    expect(host.tabs.activate).not.toHaveBeenCalled();
    await port.reset();
    expect(port.focusedHandle()).toBeUndefined();
  });

  it("admits a page-opened tab instead of opening a second one", async () => {
    const host = installHost();
    const port = new OctopLocalTabWorksetPort(host);
    const watcher = createLocalTabWatcher(port);
    const opened = await watcher.open({});
    const created = await host.tabs.create({
      url: "https://two.test",
      active: true,
    });
    const settled = await opened.settle({ timeoutMs: 100 });
    expect(settled).toEqual({
      title: "https://two.test",
      url: "https://two.test",
    });
    expect(created).toBe("tab-2");

    const admitted = await port.admit({
      title: "https://two.test",
      url: "https://two.test",
    } as OpenedPageInfo);
    expect(admitted).toBe("tab-2");
    expect(host.tabs.create).toHaveBeenCalledTimes(1);
  });

  it("creates an inactive tab when the page opened nothing new", async () => {
    const host = installHost();
    const port = new OctopLocalTabWorksetPort(host);
    const admitted = await port.admit({
      url: "https://three.test",
    } as OpenedPageInfo);
    expect(admitted).toBe("tab-2");
    expect(host.tabs.create).toHaveBeenCalledWith({
      url: "https://three.test",
      active: false,
    });
  });

  it("requires a URL to admit a page", async () => {
    const port = new OctopLocalTabWorksetPort(installHost());
    await expect(port.admit({} as OpenedPageInfo)).rejects.toThrow(/no URL/);
  });

  it("settles a tab watcher only once", async () => {
    const port = new OctopLocalTabWorksetPort(installHost());
    const watcher = createLocalTabWatcher(port);
    const opened = await watcher.open({});
    expect(await opened.settle({ timeoutMs: 0 })).toBeUndefined();
    expect(await opened.settle({ timeoutMs: 0 })).toBeUndefined();
  });

  it("closing a tab clears its focus", async () => {
    const host = installHost();
    const port = new OctopLocalTabWorksetPort(host);
    await port.focus("tab-1");
    await port.close("tab-1");
    expect(host.tabs.close).toHaveBeenCalledWith("tab-1");
    expect(port.focusedHandle()).toBeUndefined();
  });
});

describe("local downloads", () => {
  it("observes a will-download window through the main process", async () => {
    const host = installHost();
    const watcher = createLocalDownloadWatcher(host);
    const window = await watcher.open({});
    expect(host.downloads.openWindow).toHaveBeenCalled();
    expect(await window.settle({ timeoutMs: 1500 })).toBe(1);
    expect(host.downloads.settleWindow).toHaveBeenCalledWith("0", 1500);
  });

  it("reads a completed download as bytes", async () => {
    const host = installHost();
    host.downloads.list = vi.fn(async () => [
      {
        id: "dl_1",
        name: "a.txt",
        contentType: "text/plain",
        size: 5,
        at: "2026-01-01T00:00:00.000Z",
      },
    ]);
    host.downloads.read = vi.fn(async () => ({
      contentType: "text/plain",
      data: btoa("hello"),
    }));
    const reader = createLocalDownloadReader(host);
    expect(await reader.list()).toEqual([
      {
        id: "dl_1",
        name: "a.txt",
        contentType: "text/plain",
        size: 5,
        at: "2026-01-01T00:00:00.000Z",
      },
    ]);
    const file = await reader.read("dl_1");
    expect(file.contentType).toBe("text/plain");
    expect(new TextDecoder().decode(file.bytes)).toBe("hello");
  });

  it("rejects a malformed download list", async () => {
    const host = installHost();
    host.downloads.list = vi.fn(async () => [{ id: 1 } as never]);
    const reader = createLocalDownloadReader(host);
    await expect(reader.list()).rejects.toThrow(/invalid/);
  });
});

describe("local linked documents", () => {
  it("fetches inside the active tab and decodes the body", async () => {
    const host = installHost();
    const reader = createLocalLinkedDocuments(
      host,
      () => "tab-1",
      () => "https://one.test",
    );
    expect(reader.origin).toBe("https://one.test");
    const document = await reader.read("https://one.test/report");
    expect(host.frame.fetchInTab).toHaveBeenCalledWith(
      "tab-1",
      "https://one.test/report",
      {
        method: "GET",
      },
    );
    expect(document.mimeType).toBe("text/plain");
    expect(new TextDecoder().decode(document.bytes)).toBe("hello");
  });

  it("rejects non-http URLs, missing tabs, and error statuses", async () => {
    const host = installHost();
    const reader = createLocalLinkedDocuments(
      host,
      () => "tab-1",
      () => undefined,
    );
    await expect(reader.read("file:///etc/passwd")).rejects.toThrow(/http/);
    await expect(reader.read("https://" + "a".repeat(2048))).rejects.toThrow(
      /invalid/,
    );

    const unbound = createLocalLinkedDocuments(
      host,
      () => undefined,
      () => undefined,
    );
    await expect(unbound.read("https://one.test/doc")).rejects.toThrow(
      /No browser tab/,
    );

    host.frame.fetchInTab = vi.fn(async () => ({
      status: 404,
      headers: {},
      body: "",
    }));
    await expect(reader.read("https://one.test/missing")).rejects.toThrow(
      /404/,
    );
  });
});

describe("OctopPageAgentTransport in local mode", () => {
  it("routes PageAgent requests to the bound local tab", async () => {
    const host = installHost();
    const bridge = {} as OctopHostBridge;
    const transport = new OctopPageAgentTransport(
      bridge,
      { include: ["body"] },
      () => "tab-1",
    );
    const reply = await transport.send({
      type: "perceive",
      scope: { include: ["body"] },
    } as never);
    expect(reply).toEqual({
      type: "perceive-result",
      result: { nodes: [] },
      targets: [],
    });
    expect(host.frame.domRequest).toHaveBeenCalledWith(
      "tab-1",
      expect.objectContaining({
        type: "perceive",
        actionScope: { include: ["body"] },
      }),
    );
  });

  it("refuses to fall back to the backend when no tab is bound", async () => {
    installHost();
    const bridge = { call: vi.fn() } as unknown as OctopHostBridge;
    const transport = new OctopPageAgentTransport(
      bridge,
      { include: ["body"] },
      () => undefined,
    );
    await expect(
      transport.send({
        type: "perceive",
        scope: { include: ["body"] },
      } as never),
    ).rejects.toThrow(/No local browser tab is bound/);
    expect(bridge.call).not.toHaveBeenCalled();
  });
});
