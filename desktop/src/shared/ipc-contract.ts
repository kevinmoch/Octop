/**
 * Shared IPC contract between main and preload. The renderer only ever sees the
 * typed ``window.octopDesktop`` bridge; raw ``ipcRenderer`` is never exposed.
 */

export interface ClientSettings {
  /** Launch at login / open at login. */
  launchAtLogin: boolean;
  /** Close-window behaviour: quit vs. keep in tray. */
  closeBehavior: "quit" | "tray";
  /**
   * Device light/dark preference shared with the Dashboard (`system` follows
   * the OS). The Early Window paints from this; the page reports changes back.
   */
  themeBackground: "system" | "light" | "dark";
  /** Whether the shell update check should follow the stable channel only. */
  stableOnly: boolean;
  /**
   * Which browser the Dashboard's Control → Browser page shows on desktop.
   * `remote` keeps the backend streaming viewer as the rollback path.
   */
  browserMode: "local" | "remote";
}

export interface PlatformInfo {
  platform: "darwin" | "win32" | "linux";
  arch: string;
  isDesktop: true;
}

export interface UpdateCheckResult {
  status: "update-available" | "up-to-date" | "error";
  currentVersion: string;
  latestVersion: string | null;
  error: string | null;
}

/** A JWT minted by the shell from the stored device credential. */
export interface IssuedSession {
  access_token: string;
  expires_in: number;
}

/**
 * Loopback WebSocket relay (§5.2). A custom scheme cannot carry a handshake,
 * so the page dials this instead of ``octop://app/api/…``.
 */
export interface WsRelayEndpoint {
  /** ``ws://127.0.0.1:<port>/<token>`` — append ``/api/…`` to it. */
  baseUrl: string;
  /** Random per-launch path segment; the relay refuses every other path. */
  token: string;
}

/** Theme for the *native* window controls (traffic lights / caption buttons). */
export interface WindowChromeTheme {
  /** Resolved mode the page is showing. */
  mode: "light" | "dark";
  /** The user's preference; kept so "system" keeps following the OS. */
  scheme?: "system" | "light" | "dark";
}

export const IPC_CHANNELS = {
  getPlatformInfo: "octop:get-platform-info",
  openDirectoryPicker: "octop:open-directory-picker",
  openFilePicker: "octop:open-file-picker",
  onBackendReady: "octop:backend-ready",
  windowChromeTheme: "octop:window-chrome-theme",
  windowChromeFullscreen: "octop:window-chrome-fullscreen",
  clientSettingsGet: "octop:client-settings:get",
  clientSettingsSet: "octop:client-settings:set",
  wsEndpoint: "octop:ws-endpoint",
  checkForUpdates: "octop:check-for-updates",
  /** Page already confirmed. No arguments: the shell re-selects the release. */
  installUpdate: "octop:install-update",
  windowsMenuModel: "octop:windows-menu:model",
  windowsMenuInvoke: "octop:windows-menu:invoke",
  /** Linux square caption buttons. No-op on the other platforms. */
  windowMinimize: "octop:window-minimize",
  windowToggleMaximize: "octop:window-toggle-maximize",
  windowClose: "octop:window-close",
  windowIsMaximized: "octop:window-is-maximized",
  windowMaximized: "octop:window-maximized",
  sessionRegister: "octop:session:register",
  sessionRenew: "octop:session:renew",
  sessionRevoke: "octop:session:revoke",
  browserTabsList: "octop:browser:tabs:list",
  browserTabsCreate: "octop:browser:tabs:create",
  browserTabsClose: "octop:browser:tabs:close",
  browserTabsActivate: "octop:browser:tabs:activate",
  browserTabsNavigate: "octop:browser:tabs:navigate",
  browserTabsGoBack: "octop:browser:tabs:go-back",
  browserTabsGoForward: "octop:browser:tabs:go-forward",
  browserTabsReload: "octop:browser:tabs:reload",
  browserTabsSetBounds: "octop:browser:tabs:set-bounds",
  browserTabsSetVisible: "octop:browser:tabs:set-visible",
  browserTabsChanged: "octop:browser:tabs:changed",
  browserTabsEvent: "octop:browser:tabs:event",
  browserFrameDom: "octop:browser:frame:dom",
  browserFrameFetch: "octop:browser:frame:fetch",
  /** Main → tab preload: one PageAgent request. */
  browserFrameRequest: "octop:browser:frame:request",
  /** Tab preload → main: the reply to that request. */
  browserFrameResponse: "octop:browser:frame:response",
  /** Tab preload → main: native file picker for a page `attach` action. */
  browserFramePickFiles: "octop:browser:frame:pick-files",
  browserDownloadsList: "octop:browser:downloads:list",
  browserDownloadsRead: "octop:browser:downloads:read",
  browserDownloadsOpen: "octop:browser:downloads:open",
  browserDownloadsSettle: "octop:browser:downloads:settle",
} as const;
