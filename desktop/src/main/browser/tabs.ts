/** Rect in CSS pixels, matching what `getBoundingClientRect()` reports. */
export interface TabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserTab {
  id: string;
  url: string;
  title: string;
  active: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  isLoading: boolean;
}

export interface BrowserTabCreateOptions {
  url: string;
  /** Switch to the new tab. Defaults to true. */
  active?: boolean;
  /** Tab that opened this one (`window.open`, `target=_blank`). */
  openerId?: string;
}

export type BrowserTabEvent =
  | { kind: "created"; tab: BrowserTab }
  | { kind: "updated"; tab: BrowserTab }
  | { kind: "closed"; tabId: string }
  | { kind: "activated"; tabId: string }
  | { kind: "opened-by-page"; openerId: string; url: string; tabId: string };

export interface TabNavigationHistory {
  canGoBack(): boolean;
  canGoForward(): boolean;
  goBack(): void;
  goForward(): void;
}

/**
 * The subset of `Electron.WebContents` the tab manager drives. Kept structural
 * so the manager can be unit-tested without launching Electron.
 */
export interface TabWebContents {
  readonly id: number;
  /** History moved off `webContents` in Electron 45 (deprecated there). */
  readonly navigationHistory: TabNavigationHistory;
  getURL(): string;
  getTitle(): string;
  isLoading(): boolean;
  isDestroyed(): boolean;
  loadURL(url: string): Promise<void>;
  reload(): void;
  close(): void;
  /** Reset persisted per-origin page zoom when a tab is created. */
  setZoomLevel(level: number): void;
  setWindowOpenHandler(handler: (details: { url: string }) => { action: "deny" }): void;
  on(event: string, listener: (...args: never[]) => void): unknown;
  send(channel: string, payload: unknown): void;
  executeJavaScript(code: string, userGesture?: boolean): Promise<unknown>;
}

/** The subset of `Electron.WebContentsView` the tab manager drives. */
export interface TabView {
  readonly webContents: TabWebContents;
  setBounds(bounds: TabBounds): void;
  setVisible(visible: boolean): void;
}

/** The part of `Electron.BrowserWindow` the tab manager attaches views to. */
export interface TabHostWindow<View extends TabView = TabView> {
  contentView: {
    addChildView(view: View): void;
    removeChildView(view: View): void;
  };
}

export interface BrowserTabManagerOptions<View extends TabView> {
  createView(options: { preloadPath: string }): View;
  preloadPath: string;
  defaultUrl: string;
  /** Upper bound on open tabs; the risk register in the design doc uses 8. */
  maxTabs?: number;
  /**
   * Hand a non-http(s) `window.open` target (mailto:, tel:, …) to the OS. The
   * view never loads those URLs itself.
   */
  onExternalUrl?: (url: string) => void;
}

interface TabEntry<View extends TabView> {
  id: string;
  view: View;
  openerId?: string;
}

const DEFAULT_MAX_TABS = 8;
const EMPTY_BOUNDS: TabBounds = { x: 0, y: 0, width: 0, height: 0 };

/**
 * Turn address-bar input into an http(s) URL.
 *
 * Only http(s) reaches a tab, so `javascript:`, `file:` and `data:` never
 * become a navigation. A bare host gets `https://`.
 */
export function normalizeTabUrl(input: string, fallback: string): string {
  const text = input.trim();
  if (!text) return fallback;
  const candidate = /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new Error("Enter a valid http(s) address");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Only http and https pages can be opened");
  }
  return url.toString();
}

const DANGEROUS_PROTOCOLS = new Set([
  "javascript:",
  "data:",
  "file:",
  "about:",
  "blob:",
  "octop:",
  "devtools:",
]);

/**
 * A `window.open` target that belongs to an OS-registered handler rather than
 * a tab (`mailto:`, `tel:`, custom app schemes). http(s) stays in the tabs.
 */
export function externalAppUrl(raw: string): string | undefined {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return undefined;
  }
  if (url.protocol === "http:" || url.protocol === "https:") return undefined;
  if (DANGEROUS_PROTOCOLS.has(url.protocol)) return undefined;
  if (!/^[a-z][a-z0-9+.-]*:$/.test(url.protocol)) return undefined;
  return url.toString();
}

/**
 * Convert renderer CSS pixels to native DIP for `WebContentsView.setBounds`.
 *
 * A zoomed renderer reports `getBoundingClientRect()` in CSS px, but the native
 * view is positioned in DIP: at zoom 25% the CSS width is 4x the DIP width, so
 * passing the raw rect makes the browser view cover the whole window. The
 * Dashboard's `webContents.getZoomFactor()` is the conversion factor.
 */
export function scaleBoundsForZoom(bounds: TabBounds, zoomFactor: number): TabBounds {
  const factor = Number.isFinite(zoomFactor) && zoomFactor > 0 ? zoomFactor : 1;
  return {
    x: bounds.x * factor,
    y: bounds.y * factor,
    width: bounds.width * factor,
    height: bounds.height * factor,
  };
}

/**
 * Local browser tabs for the desktop shell.
 *
 * Each tab is a `WebContentsView` stacked on the Dashboard window. The renderer
 * owns the tab strip and the address bar; this class owns navigation, lifecycle
 * and `window.open` interception. The view is only shown while the renderer is
 * on the browser page AND an overlay/modal is not covering it.
 */
export class BrowserTabManager<View extends TabView = TabView> {
  private readonly tabs = new Map<string, TabEntry<View>>();
  private readonly bounds = new Map<string, TabBounds>();
  private readonly changedListeners = new Set<() => void>();
  private readonly eventListeners = new Set<(event: BrowserTabEvent) => void>();
  private readonly hostWindow: TabHostWindow<View>;
  private readonly options: BrowserTabManagerOptions<View>;
  private readonly maxTabs: number;
  private activeId: string | undefined;
  private nextId = 1;
  private hostVisible = false;

  constructor(hostWindow: TabHostWindow<View>, options: BrowserTabManagerOptions<View>) {
    this.hostWindow = hostWindow;
    this.options = options;
    this.maxTabs = options.maxTabs ?? DEFAULT_MAX_TABS;
  }

  list(): BrowserTab[] {
    return [...this.tabs.values()].map((entry) => this.toTab(entry));
  }

  activeTabId(): string | undefined {
    return this.activeId;
  }

  create(options: BrowserTabCreateOptions): string {
    if (this.tabs.size >= this.maxTabs) {
      throw new Error(`At most ${this.maxTabs} browser tabs can be open`);
    }
    const id = `tab-${this.nextId++}`;
    const view = this.options.createView({ preloadPath: this.options.preloadPath });
    const entry: TabEntry<View> = { id, view, openerId: options.openerId };
    this.tabs.set(id, entry);
    this.bounds.set(id, EMPTY_BOUNDS);
    // A tab inherits Chromium's per-origin zoom from previous sessions; start
    // every new tab at 100% so page content and view bounds agree.
    view.webContents.setZoomLevel(0);

    view.webContents.setWindowOpenHandler(({ url }) => {
      this.openFromPage(entry, url);
      return { action: "deny" };
    });
    for (const event of [
      "did-navigate",
      "did-navigate-in-page",
      "page-title-updated",
      "did-start-loading",
      "did-stop-loading",
    ]) {
      view.webContents.on(event, () => this.publishUpdate(entry));
    }
    view.webContents.on("destroyed", () => {
      if (this.tabs.has(id)) this.close(id);
    });

    this.hostWindow.contentView.addChildView(view);
    view.setBounds(EMPTY_BOUNDS);
    view.setVisible(false);
    void view.webContents
      .loadURL(this.resolveUrl(options.url))
      // Chromium restores the persisted per-origin zoom on commit; reset again
      // once the page has loaded so a fresh tab shows content at 100%.
      .then(() => {
        if (!view.webContents.isDestroyed()) view.webContents.setZoomLevel(0);
      })
      .catch(() => undefined);

    if (options.active !== false) this.activate(id);
    else this.publishUpdate(entry);

    this.emit({ kind: "created", tab: this.toTab(entry) });
    return id;
  }

  close(tabId: string): void {
    const entry = this.tabs.get(tabId);
    if (!entry) return;
    this.tabs.delete(tabId);
    this.bounds.delete(tabId);
    this.hostWindow.contentView.removeChildView(entry.view);
    if (!entry.view.webContents.isDestroyed()) entry.view.webContents.close();
    if (this.activeId === tabId) {
      const next = this.tabs.keys().next();
      this.activeId = next.done ? undefined : next.value;
      if (this.activeId) this.applyActiveView();
    }
    this.emit({ kind: "closed", tabId });
    this.notifyChanged();
  }

  activate(tabId: string): void {
    if (!this.tabs.has(tabId)) return;
    this.activeId = tabId;
    this.applyActiveView();
    this.emit({ kind: "activated", tabId });
    this.notifyChanged();
  }

  navigate(tabId: string, url: string): void {
    const entry = this.tabs.get(tabId);
    if (!entry) return;
    void entry.view.webContents.loadURL(this.resolveUrl(url)).catch(() => undefined);
  }

  goBack(tabId: string): void {
    const history = this.tabs.get(tabId)?.view.webContents.navigationHistory;
    if (history?.canGoBack()) history.goBack();
  }

  goForward(tabId: string): void {
    const history = this.tabs.get(tabId)?.view.webContents.navigationHistory;
    if (history?.canGoForward()) history.goForward();
  }

  reload(tabId: string): void {
    this.tabs.get(tabId)?.view.webContents.reload();
  }

  /** Place one tab's view. Bounds are remembered so reactivation reuses them. */
  setViewportBounds(tabId: string, bounds: TabBounds): void {
    const entry = this.tabs.get(tabId);
    if (!entry) return;
    const next = {
      x: Math.round(bounds.x),
      y: Math.round(bounds.y),
      width: Math.round(bounds.width),
      height: Math.round(bounds.height),
    };
    this.bounds.set(tabId, next);
    if (this.activeId === tabId) entry.view.setBounds(next);
  }

  /**
   * The renderer is on the browser page and the viewport is uncovered. Views are
   * stacked above the Dashboard window, so they must be hidden the moment the
   * user navigates away or opens a shell-level overlay.
   */
  setHostVisible(visible: boolean): void {
    this.hostVisible = visible;
    this.applyActiveView();
  }

  getWebContents(tabId: string): TabWebContents | undefined {
    return this.tabs.get(tabId)?.view.webContents;
  }

  /** True when this webContents belongs to one of the managed tabs. */
  ownsWebContents(webContentsId: number): boolean {
    return this.tabIdForWebContents(webContentsId) !== undefined;
  }

  tabIdForWebContents(webContentsId: number): string | undefined {
    for (const entry of this.tabs.values()) {
      if (entry.view.webContents.id === webContentsId) return entry.id;
    }
    return undefined;
  }

  onChanged(listener: () => void): () => void {
    this.changedListeners.add(listener);
    return () => this.changedListeners.delete(listener);
  }

  onEvent(listener: (event: BrowserTabEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  /**
   * Drop every tab without touching the host window. Called when the window is
   * already being destroyed, where `removeChildView` would throw.
   */
  dispose(): void {
    // Detach first: closing a webContents emits `destroyed`, whose listener
    // would otherwise call close() and touch the dying host window.
    const entries = [...this.tabs.values()];
    this.tabs.clear();
    this.bounds.clear();
    this.activeId = undefined;
    for (const entry of entries) {
      if (!entry.view.webContents.isDestroyed()) entry.view.webContents.close();
    }
    this.changedListeners.clear();
    this.eventListeners.clear();
  }

  private openFromPage(opener: TabEntry<View>, rawUrl: string): void {
    const external = externalAppUrl(rawUrl);
    if (external) {
      this.options.onExternalUrl?.(external);
      return;
    }
    let target: string;
    try {
      target = normalizeTabUrl(rawUrl, this.options.defaultUrl);
    } catch {
      return;
    }
    let tabId: string;
    try {
      tabId = this.create({ url: target, active: true, openerId: opener.id });
    } catch {
      return;
    }
    this.emit({ kind: "opened-by-page", openerId: opener.id, url: target, tabId });
  }

  private resolveUrl(input: string): string {
    return normalizeTabUrl(input, this.options.defaultUrl);
  }

  private applyActiveView(): void {
    for (const entry of this.tabs.values()) {
      const isActive = entry.id === this.activeId;
      // Bounds are cheap to apply while hidden and keep a reactivated tab sized
      // correctly even when the renderer has not re-measured yet.
      if (isActive) entry.view.setBounds(this.bounds.get(entry.id) ?? EMPTY_BOUNDS);
      entry.view.setVisible(this.hostVisible && isActive);
    }
  }

  private publishUpdate(entry: TabEntry<View>): void {
    this.emit({ kind: "updated", tab: this.toTab(entry) });
    this.notifyChanged();
  }

  private toTab(entry: TabEntry<View>): BrowserTab {
    const contents = entry.view.webContents;
    return {
      id: entry.id,
      url: contents.getURL(),
      title: contents.getTitle() || contents.getURL(),
      active: entry.id === this.activeId,
      canGoBack: contents.navigationHistory.canGoBack(),
      canGoForward: contents.navigationHistory.canGoForward(),
      isLoading: contents.isLoading(),
    };
  }

  private notifyChanged(): void {
    for (const listener of [...this.changedListeners]) listener();
  }

  private emit(event: BrowserTabEvent): void {
    for (const listener of [...this.eventListeners]) listener(event);
  }
}
