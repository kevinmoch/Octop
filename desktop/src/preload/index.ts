import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";
import {
  IPC_CHANNELS,
  type ClientSettings,
  type IssuedSession,
  type PlatformInfo,
  type UpdateCheckResult,
  type WindowChromeTheme,
  type WsRelayEndpoint,
} from "../shared/ipc-contract";

/**
 * Minimal, typed bridge exposed to the sandboxed renderer as ``window.octopDesktop``.
 * No raw ipcRenderer, no file system, no shell execution, no arbitrary URLs.
 */

function subscribe<T>(channel: string, cb: (payload: T) => void): () => void {
  const listener = (_event: IpcRendererEvent, payload: T) => cb(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

/**
 * Read once and memoise: the relay binds before the window loads, so the value
 * cannot change during a launch, and URL builders call this from render paths
 * where awaiting is not an option.
 */
let wsEndpointCache: WsRelayEndpoint | null | undefined;

const bridge = {
  getPlatformInfo(): Promise<PlatformInfo> {
    return ipcRenderer.invoke(IPC_CHANNELS.getPlatformInfo);
  },
  openDirectoryPicker(): Promise<string | null> {
    return ipcRenderer.invoke(IPC_CHANNELS.openDirectoryPicker);
  },
  openFilePicker(): Promise<string | null> {
    return ipcRenderer.invoke(IPC_CHANNELS.openFilePicker);
  },
  onBackendReady(callback: (ready: { ok: boolean }) => void): () => void {
    return subscribe(IPC_CHANNELS.onBackendReady, callback);
  },
  /**
   * Report the page theme so the *native* window controls (traffic lights,
   * Windows caption buttons) match it. Linux draws its own square buttons and
   * drives them through ``windowControls``.
   */
  setWindowChrome(theme: WindowChromeTheme): void {
    ipcRenderer.send(IPC_CHANNELS.windowChromeTheme, theme);
  },
  /**
   * Linux caption buttons. The shell ignores these on macOS and Windows, where
   * the buttons stay native.
   */
  windowControls: {
    minimize(): void {
      ipcRenderer.send(IPC_CHANNELS.windowMinimize);
    },
    toggleMaximize(): void {
      ipcRenderer.send(IPC_CHANNELS.windowToggleMaximize);
    },
    close(): void {
      ipcRenderer.send(IPC_CHANNELS.windowClose);
    },
    isMaximized(): Promise<boolean> {
      return ipcRenderer.invoke(IPC_CHANNELS.windowIsMaximized);
    },
    onMaximizedChange(callback: (maximized: boolean) => void): () => void {
      return subscribe(IPC_CHANNELS.windowMaximized, callback);
    },
  },
  /**
   * Windows draws no native menu bar under the transparent caption, so the
   * page draws the titles and the submenus, then asks the shell to run a command.
   */
  windowsMenu: {
    model(): Promise<{
      titles: string[];
      menus: Array<Array<{ kind: "separator" } | { kind: "command"; id: string; label: string; accelerator: string | null }>>;
    } | null> {
      return ipcRenderer.invoke(IPC_CHANNELS.windowsMenuModel);
    },
    invoke(id: string): Promise<void> {
      return ipcRenderer.invoke(IPC_CHANNELS.windowsMenuInvoke, id);
    },
  },
  /**
   * Full screen hides the macOS traffic lights, so the page stops reserving
   * room for them. Fires only on change; the app never starts full screen.
   */
  onWindowFullscreenChange(callback: (fullscreen: boolean) => void): () => void {
    return subscribe(IPC_CHANNELS.windowChromeFullscreen, callback);
  },
  clientSettings: {
    get(): Promise<ClientSettings> {
      return ipcRenderer.invoke(IPC_CHANNELS.clientSettingsGet);
    },
    set(patch: Partial<ClientSettings>): Promise<ClientSettings> {
      return ipcRenderer.invoke(IPC_CHANNELS.clientSettingsSet, patch);
    },
  },
  /**
   * Where to dial WebSockets (§5.2). ``null`` when the shell has no relay, in
   * which case the Dashboard keeps deriving the address from the page origin.
   */
  wsEndpoint(): WsRelayEndpoint | null {
    if (wsEndpointCache === undefined) {
      wsEndpointCache = (ipcRenderer.sendSync(IPC_CHANNELS.wsEndpoint) as WsRelayEndpoint | null) ?? null;
    }
    return wsEndpointCache;
  },
  checkForUpdates(): Promise<UpdateCheckResult> {
    return ipcRenderer.invoke(IPC_CHANNELS.checkForUpdates);
  },
  installUpdate(): Promise<void> {
    return ipcRenderer.invoke(IPC_CHANNELS.installUpdate);
  },
  /**
   * Device session (§7 accounts). The renderer can ask for a JWT, but the
   * credential behind it never crosses this bridge.
   */
  session: {
    register(accessToken: string): Promise<boolean> {
      return ipcRenderer.invoke(IPC_CHANNELS.sessionRegister, accessToken);
    },
    renew(): Promise<IssuedSession | null> {
      return ipcRenderer.invoke(IPC_CHANNELS.sessionRenew);
    },
    revoke(accessToken: string): Promise<boolean> {
      return ipcRenderer.invoke(IPC_CHANNELS.sessionRevoke, accessToken);
    },
  },
  browserHost: {
    tabs: {
      list: () => ipcRenderer.invoke(IPC_CHANNELS.browserTabsList),
      create: (options: { url: string; active?: boolean }) =>
        ipcRenderer.invoke(IPC_CHANNELS.browserTabsCreate, options),
      close: (tabId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserTabsClose, tabId),
      activate: (tabId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserTabsActivate, tabId),
      navigate: (tabId: string, url: string) =>
        ipcRenderer.invoke(IPC_CHANNELS.browserTabsNavigate, { tabId, url }),
      goBack: (tabId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserTabsGoBack, tabId),
      goForward: (tabId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserTabsGoForward, tabId),
      reload: (tabId: string) => ipcRenderer.invoke(IPC_CHANNELS.browserTabsReload, tabId),
      setViewportBounds: (
        tabId: string,
        bounds: { x: number; y: number; width: number; height: number },
      ) => ipcRenderer.send(IPC_CHANNELS.browserTabsSetBounds, { tabId, bounds }),
      setVisible: (visible: boolean) =>
        ipcRenderer.send(IPC_CHANNELS.browserTabsSetVisible, visible),
      onChanged: (callback: () => void) => subscribe(IPC_CHANNELS.browserTabsChanged, callback),
      onEvent: (callback: (event: unknown) => void) => subscribe(IPC_CHANNELS.browserTabsEvent, callback),
    },
    frame: {
      domRequest: (tabId: string, request: unknown) =>
        ipcRenderer.invoke(IPC_CHANNELS.browserFrameDom, { tabId, request }),
      fetchInTab: (tabId: string, url: string, init?: unknown) =>
        ipcRenderer.invoke(IPC_CHANNELS.browserFrameFetch, { tabId, url, init }),
    },
    downloads: {
      list: () => ipcRenderer.invoke(IPC_CHANNELS.browserDownloadsList),
      read: (id: string) => ipcRenderer.invoke(IPC_CHANNELS.browserDownloadsRead, id),
      openWindow: () => ipcRenderer.invoke(IPC_CHANNELS.browserDownloadsOpen),
      settleWindow: (token: string, timeoutMs: number) =>
        ipcRenderer.invoke(IPC_CHANNELS.browserDownloadsSettle, { token, timeoutMs }),
    },
  },
} as const;

export type OctopDesktopBridge = typeof bridge;

contextBridge.exposeInMainWorld("octopDesktop", bridge);

// The failure page's Retry lives in this same window (the splash is not a
// second BrowserWindow). Restart stays off `octopDesktop`. The channel string
// matches `EARLY_IPC.restart`; importing that module would split this preload.
contextBridge.exposeInMainWorld("octopShell", {
  restart(): void {
    ipcRenderer.send("octop:early-restart");
  },
});
