import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getDesktopBridge,
  type DesktopBrowserHost,
  type DesktopBrowserTab,
} from "../utils/desktopBridge";

export const DEFAULT_LOCAL_BROWSER_URL = "https://jijian.huawei.com/";

export interface BrowserTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface UseBrowserTabsOptions {
  /** Called for failed tab operations (limit reached, shell error, ...). */
  onError?: (message: string) => void;
  /**
   * First tab's URL (config.json `desktop_browser_url`). Auto-creation waits
   * for it so the configured start page is not replaced by the fallback.
   */
  defaultUrl?: string | null;
}

export interface UseBrowserTabsResult {
  /** Null outside the desktop shell. */
  host: DesktopBrowserHost | null;
  tabs: DesktopBrowserTab[];
  activeTab: DesktopBrowserTab | undefined;
  createTab(url?: string): Promise<void>;
  closeTab(tabId: string): Promise<void>;
  activateTab(tabId: string): Promise<void>;
  navigate(tabId: string, url: string): Promise<void>;
  goBack(tabId: string): Promise<void>;
  goForward(tabId: string): Promise<void>;
  reload(tabId: string): Promise<void>;
  setViewport(tabId: string, bounds: BrowserTabBounds): void;
  setVisible(visible: boolean): void;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/**
 * Tab state for the desktop local browser.
 *
 * The main process owns navigation and lifecycle; this hook mirrors the list it
 * reports and forwards user intent. The first tab is opened automatically so an
 * empty browser page becomes usable without an extra click.
 */
export function useBrowserTabs(
  options: UseBrowserTabsOptions = {},
): UseBrowserTabsResult {
  const host = useMemo(() => getDesktopBridge()?.browserHost ?? null, []);
  const [tabs, setTabs] = useState<DesktopBrowserTab[]>([]);
  const [loaded, setLoaded] = useState(false);
  const creating = useRef(false);
  // Kept in a ref so a changing callback identity does not resubscribe IPC.
  const onError = useRef(options.onError);
  onError.current = options.onError;

  const fail = useCallback((reason: unknown) => {
    onError.current?.(errorMessage(reason));
  }, []);

  const refresh = useCallback(async () => {
    if (!host) return;
    try {
      setTabs(await host.tabs.list());
    } catch (reason) {
      fail(reason);
    } finally {
      setLoaded(true);
    }
  }, [host, fail]);

  useEffect(() => {
    if (!host) {
      setLoaded(true);
      return;
    }
    void refresh();
    const offChanged = host.tabs.onChanged(() => void refresh());
    const offEvent = host.tabs.onEvent(() => void refresh());
    return () => {
      offChanged();
      offEvent();
    };
  }, [host, refresh]);

  const startUrl = options.defaultUrl ?? null;

  useEffect(() => {
    if (!host || !loaded || !startUrl || tabs.length > 0 || creating.current)
      return;
    creating.current = true;
    void host.tabs
      .create({ url: startUrl, active: true })
      .then(() => refresh())
      .catch(fail)
      .finally(() => {
        creating.current = false;
      });
  }, [host, loaded, startUrl, tabs.length, refresh, fail]);

  const guarded = useCallback(
    async (operation: () => Promise<void> | void) => {
      try {
        await operation();
        await refresh();
      } catch (reason) {
        fail(reason);
      }
    },
    [refresh, fail],
  );

  const createTab = useCallback(
    (url = options.defaultUrl ?? DEFAULT_LOCAL_BROWSER_URL) =>
      guarded(async () => {
        if (!host) return;
        await host.tabs.create({ url, active: true });
      }),
    [guarded, host, options.defaultUrl],
  );

  const closeTab = useCallback(
    (tabId: string) =>
      guarded(async () => {
        await host?.tabs.close(tabId);
      }),
    [guarded, host],
  );

  const activateTab = useCallback(
    (tabId: string) =>
      guarded(async () => {
        await host?.tabs.activate(tabId);
      }),
    [guarded, host],
  );

  const navigate = useCallback(
    (tabId: string, url: string) =>
      guarded(async () => {
        await host?.tabs.navigate(tabId, url);
      }),
    [guarded, host],
  );

  const goBack = useCallback(
    (tabId: string) =>
      guarded(async () => {
        await host?.tabs.goBack(tabId);
      }),
    [guarded, host],
  );

  const goForward = useCallback(
    (tabId: string) =>
      guarded(async () => {
        await host?.tabs.goForward(tabId);
      }),
    [guarded, host],
  );

  const reload = useCallback(
    (tabId: string) =>
      guarded(async () => {
        await host?.tabs.reload(tabId);
      }),
    [guarded, host],
  );

  const setViewport = useCallback(
    (tabId: string, bounds: BrowserTabBounds) => {
      host?.tabs.setViewportBounds(tabId, bounds);
    },
    [host],
  );

  const setVisible = useCallback(
    (visible: boolean) => {
      host?.tabs.setVisible(visible);
    },
    [host],
  );

  const activeTab = useMemo(
    () => tabs.find((tab) => tab.active) ?? tabs[0],
    [tabs],
  );

  return {
    host,
    tabs,
    activeTab,
    createTab,
    closeTab,
    activateTab,
    navigate,
    goBack,
    goForward,
    reload,
    setViewport,
    setVisible,
  };
}
