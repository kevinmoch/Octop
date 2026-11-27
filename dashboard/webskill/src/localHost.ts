/**
 * Access to the Electron shell's local-browser bridge from inside the WebSkill
 * iframe.
 *
 * The iframe is same-origin with the Dashboard, so `window.parent.octopDesktop`
 * is the preload bridge. Server mode has no bridge and every caller falls back
 * to the backend WebSocket / HTTP host APIs.
 */

export interface LocalBrowserTab {
  id: string;
  url: string;
  title: string;
  active: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  isLoading: boolean;
}

export interface LocalDownloadEntry {
  id: string;
  name: string;
  contentType: string;
  size: number;
  at: string;
}

export interface LocalTabFetchResult {
  status: number;
  headers: Record<string, string>;
  /** Base64 of the response body. */
  body: string;
}

export interface LocalBrowserHost {
  tabs: {
    list(): Promise<LocalBrowserTab[]>;
    create(options: { url: string; active?: boolean }): Promise<string>;
    close(tabId: string): Promise<void>;
    activate(tabId: string): Promise<void>;
    navigate(tabId: string, url: string): Promise<void>;
    goBack(tabId: string): Promise<void>;
    goForward(tabId: string): Promise<void>;
    reload(tabId: string): Promise<void>;
    setViewportBounds(
      tabId: string,
      bounds: { x: number; y: number; width: number; height: number },
    ): void;
    setVisible(visible: boolean): void;
    onChanged(callback: () => void): () => void;
    onEvent(callback: (event: unknown) => void): () => void;
  };
  frame: {
    domRequest(tabId: string, request: unknown): Promise<unknown>;
    fetchInTab(
      tabId: string,
      url: string,
      init?: unknown,
    ): Promise<LocalTabFetchResult>;
  };
  downloads: {
    list(): Promise<LocalDownloadEntry[]>;
    read(id: string): Promise<{ contentType: string; data: string }>;
    openWindow(): Promise<string>;
    settleWindow(token: string, timeoutMs: number): Promise<number>;
  };
}

export function localBrowserHost(): LocalBrowserHost | undefined {
  if (typeof window === "undefined") return undefined;
  const parent = window.parent as unknown as
    | { octopDesktop?: { browserHost?: LocalBrowserHost } }
    | undefined;
  return parent?.octopDesktop?.browserHost;
}

/** Base64 → bytes; shared by every local-bridge reader. */
export function decodeBase64(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}
