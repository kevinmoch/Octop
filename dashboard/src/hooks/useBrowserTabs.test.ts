import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useBrowserTabs } from "./useBrowserTabs";
import type {
  DesktopBrowserHost,
  DesktopBrowserTab,
  OctopDesktopBridge,
} from "../utils/desktopBridge";

const START_URL = "https://start.test/";

function tab(id: string, url: string, active: boolean): DesktopBrowserTab {
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

function installBrowserHost(
  overrides: Partial<DesktopBrowserHost["tabs"]> = {},
) {
  let tabs: DesktopBrowserTab[] = [];
  const listeners = new Set<() => void>();
  const notify = () => {
    for (const listener of [...listeners]) listener();
  };
  const host: DesktopBrowserHost = {
    tabs: {
      list: async () => tabs.map((entry) => ({ ...entry })),
      create: vi.fn(
        async ({ url, active = true }: { url: string; active?: boolean }) => {
          if (active) tabs = tabs.map((entry) => ({ ...entry, active: false }));
          const created = tab(`tab-${tabs.length + 1}`, url, active);
          tabs = [...tabs, created];
          notify();
          return created.id;
        },
      ),
      close: vi.fn(async (tabId: string) => {
        tabs = tabs.filter((entry) => entry.id !== tabId);
        notify();
      }),
      activate: vi.fn(async (tabId: string) => {
        tabs = tabs.map((entry) => ({ ...entry, active: entry.id === tabId }));
        notify();
      }),
      navigate: vi.fn(async (tabId: string, url: string) => {
        tabs = tabs.map((entry) =>
          entry.id === tabId ? { ...entry, url, title: url } : entry,
        );
        notify();
      }),
      goBack: vi.fn(async () => undefined),
      goForward: vi.fn(async () => undefined),
      reload: vi.fn(async () => undefined),
      setViewportBounds: vi.fn(),
      setVisible: vi.fn(),
      onChanged: (callback: () => void) => {
        listeners.add(callback);
        return () => listeners.delete(callback);
      },
      onEvent: () => () => undefined,
      ...overrides,
    },
    frame: {
      domRequest: async () => ({}),
      fetchInTab: async () => ({ status: 200, headers: {}, body: "" }),
    },
    downloads: {
      list: async () => [],
      read: async () => ({ contentType: "", data: "" }),
      openWindow: async () => "0",
      settleWindow: async () => 0,
    },
  };
  window.octopDesktop = {
    browserHost: host,
  } as unknown as OctopDesktopBridge;
  return {
    host,
    setTabs: (next: DesktopBrowserTab[]) => (tabs = next),
    notify,
  };
}

describe("useBrowserTabs", () => {
  it("opens a first tab when the browser is empty and marks it active", async () => {
    const { host } = installBrowserHost();
    const { result } = renderHook(() =>
      useBrowserTabs({ defaultUrl: START_URL }),
    );

    await waitFor(() => expect(result.current.tabs).toHaveLength(1));
    expect(host.tabs.create).toHaveBeenCalledWith({
      url: START_URL,
      active: true,
    });
    expect(result.current.activeTab?.active).toBe(true);
  });

  it("mirrors tab changes reported by the main process", async () => {
    const { setTabs, notify } = installBrowserHost();
    const { result } = renderHook(() =>
      useBrowserTabs({ defaultUrl: START_URL }),
    );
    await waitFor(() => expect(result.current.tabs).toHaveLength(1));

    await act(async () => {
      setTabs([
        tab("tab-1", "https://one.test", false),
        tab("tab-2", "https://two.test", true),
      ]);
      notify();
    });
    await waitFor(() => expect(result.current.tabs).toHaveLength(2));
    expect(result.current.activeTab?.id).toBe("tab-2");
  });

  it("forwards user intent and refreshes the list", async () => {
    const { host } = installBrowserHost();
    const { result } = renderHook(() =>
      useBrowserTabs({ defaultUrl: START_URL }),
    );
    await waitFor(() => expect(result.current.tabs).toHaveLength(1));

    await act(async () => {
      await result.current.navigate("tab-1", "https://three.test");
    });
    expect(host.tabs.navigate).toHaveBeenCalledWith(
      "tab-1",
      "https://three.test",
    );
    expect(result.current.activeTab?.url).toBe("https://three.test");

    await act(async () => {
      await result.current.createTab("https://four.test");
    });
    expect(result.current.tabs).toHaveLength(2);
    expect(result.current.activeTab?.url).toBe("https://four.test");

    await act(async () => {
      await result.current.closeTab("tab-2");
    });
    expect(host.tabs.close).toHaveBeenCalledWith("tab-2");
    expect(result.current.tabs).toHaveLength(1);

    await act(async () => {
      await result.current.activateTab("tab-1");
      await result.current.goBack("tab-1");
      await result.current.goForward("tab-1");
      await result.current.reload("tab-1");
    });
    expect(host.tabs.activate).toHaveBeenCalledWith("tab-1");
    expect(host.tabs.goBack).toHaveBeenCalledWith("tab-1");
    expect(host.tabs.goForward).toHaveBeenCalledWith("tab-1");
    expect(host.tabs.reload).toHaveBeenCalledWith("tab-1");
  });

  it("reports failures instead of throwing", async () => {
    const onError = vi.fn();
    installBrowserHost({
      create: async () => {
        throw new Error("At most 8 browser tabs can be open");
      },
    });
    const { result } = renderHook(() =>
      useBrowserTabs({ onError, defaultUrl: START_URL }),
    );
    await waitFor(() => expect(result.current.host).not.toBeNull());

    await act(async () => {
      await result.current.createTab("https://five.test");
    });
    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith(
        "At most 8 browser tabs can be open",
      ),
    );
  });

  it("forwards viewport bounds and visibility", async () => {
    const { host } = installBrowserHost();
    const { result } = renderHook(() =>
      useBrowserTabs({ defaultUrl: START_URL }),
    );
    await waitFor(() => expect(result.current.tabs).toHaveLength(1));

    act(() => {
      result.current.setViewport("tab-1", { x: 1, y: 2, width: 3, height: 4 });
      result.current.setVisible(true);
      result.current.setVisible(false);
    });
    expect(host.tabs.setViewportBounds).toHaveBeenCalledWith("tab-1", {
      x: 1,
      y: 2,
      width: 3,
      height: 4,
    });
    expect(host.tabs.setVisible).toHaveBeenNthCalledWith(1, true);
    expect(host.tabs.setVisible).toHaveBeenNthCalledWith(2, false);
  });

  it("waits for the configured start page before opening the first tab", async () => {
    const { host } = installBrowserHost();
    const { result, rerender } = renderHook(
      ({ defaultUrl }: { defaultUrl: string | null }) =>
        useBrowserTabs({ defaultUrl }),
      { initialProps: { defaultUrl: null as string | null } },
    );
    await waitFor(() => expect(result.current.host).not.toBeNull());
    expect(host.tabs.create).not.toHaveBeenCalled();

    rerender({ defaultUrl: "https://configured.test/" });
    await waitFor(() => expect(result.current.tabs).toHaveLength(1));
    expect(host.tabs.create).toHaveBeenCalledWith({
      url: "https://configured.test/",
      active: true,
    });
  });

  it("stays empty outside the desktop shell", async () => {
    delete window.octopDesktop;
    const { result } = renderHook(() =>
      useBrowserTabs({ defaultUrl: START_URL }),
    );
    await waitFor(() => expect(result.current.host).toBeNull());
    expect(result.current.tabs).toEqual([]);
    expect(result.current.activeTab).toBeUndefined();
  });
});
