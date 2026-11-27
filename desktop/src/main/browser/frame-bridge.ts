import { IPC_CHANNELS } from "../../shared/ipc-contract";
import type { TabWebContents } from "./tabs";

export interface FrameTargets {
  getWebContents(tabId: string): TabWebContents | undefined;
}

export interface TabFetchRequest {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
}

export interface TabFetchResult {
  status: number;
  headers: Record<string, string>;
  /** Base64 of the response body. */
  body: string;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  webContentsId: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]);
const MAX_FETCH_BODY_CHARS = 28_000_000;

/**
 * Routes WebSkill PageAgent requests from the Dashboard to a local browser tab.
 *
 * The main process only forwards: the tab preload owns the real PageAgent
 * handler, and every reply is matched to the tab webContents it came from. The
 * class takes the IPC event plumbing as arguments so it runs under plain Node in
 * tests; `main/index.ts` owns the `ipcMain` wiring and the Dashboard-origin gate.
 */
export class FrameBridge {
  private readonly pending = new Map<string, PendingRequest>();
  private readonly targets: FrameTargets;
  private readonly timeoutMs: number;

  constructor(targets: FrameTargets, options: { timeoutMs?: number } = {}) {
    this.targets = targets;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  /** Send one PageAgent request to `tabId` and resolve with its reply. */
  async request(tabId: string, request: unknown): Promise<unknown> {
    const webContents = this.targets.getWebContents(tabId);
    if (!webContents || webContents.isDestroyed()) {
      throw new Error("The browser tab is unavailable");
    }
    const requestId = globalThis.crypto.randomUUID();
    const result = new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        reject(new Error("The PageAgent request timed out"));
      }, this.timeoutMs);
      this.pending.set(requestId, { resolve, reject, timer, webContentsId: webContents.id });
    });
    webContents.send(IPC_CHANNELS.browserFrameRequest, { requestId, request });
    return result;
  }

  /** Resolve a reply coming from a tab preload. Unknown senders are ignored. */
  handleResponse(senderWebContentsId: number, message: unknown): void {
    if (typeof message !== "object" || message === null) return;
    const value = message as Record<string, unknown>;
    const requestId = typeof value.requestId === "string" ? value.requestId : "";
    const waiter = this.pending.get(requestId);
    if (!waiter) return;
    // A tab may only answer its own request; a different tab cannot spoof one.
    if (waiter.webContentsId !== senderWebContentsId) return;
    this.pending.delete(requestId);
    clearTimeout(waiter.timer);
    if (value.ok === true) waiter.resolve(value.reply);
    else waiter.reject(new Error(typeof value.error === "string" ? value.error : "PageAgent request failed"));
  }

  /**
   * Run `fetch` inside the tab so the request carries the page's cookies and
   * origin. Cross-origin reads go through here instead of the Dashboard origin
   * (design doc §4.1.4).
   */
  async fetchInTab(tabId: string, url: string, init?: TabFetchRequest): Promise<TabFetchResult> {
    const webContents = this.targets.getWebContents(tabId);
    if (!webContents || webContents.isDestroyed()) {
      throw new Error("The browser tab is unavailable");
    }
    const target = parseHttpUrl(url);
    const method = (init?.method ?? "GET").toUpperCase();
    if (!ALLOWED_METHODS.has(method)) throw new Error("Unsupported fetch method");
    const headers = sanitizeHeaders(init?.headers);
    if (init?.body !== undefined && init.body.length > MAX_FETCH_BODY_CHARS) {
      throw new Error("The request body is too large");
    }
    const expression = `(async () => {
      const response = await fetch(${JSON.stringify(target)}, {
        method: ${JSON.stringify(method)},
        headers: ${JSON.stringify(headers)},
        body: ${init?.body === undefined ? "undefined" : JSON.stringify(init.body)},
        credentials: "include"
      });
      const buffer = await response.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      let binary = "";
      for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
      return {
        status: response.status,
        headers: Object.fromEntries(response.headers.entries()),
        body: btoa(binary)
      };
    })()`;
    const reply = await webContents.executeJavaScript(expression, true);
    return readFetchResult(reply);
  }

  /** Reject every in-flight request, e.g. when the Dashboard window goes away. */
  dispose(): void {
    for (const [requestId, waiter] of this.pending) {
      this.pending.delete(requestId);
      clearTimeout(waiter.timer);
      waiter.reject(new Error("The browser bridge is closed"));
    }
  }
}

function parseHttpUrl(input: string): string {
  let url: URL;
  try {
    url = new URL(input);
  } catch {
    throw new Error("Enter a valid http(s) address");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Only http and https pages can be read");
  }
  return url.toString();
}

function sanitizeHeaders(input: unknown): Record<string, string> {
  if (input === undefined) return {};
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    throw new Error("Invalid request headers");
  }
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(input)) {
    if (typeof value !== "string") throw new Error("Invalid request header value");
    if (!/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(name)) throw new Error("Invalid request header name");
    headers[name] = value;
  }
  return headers;
}

function readFetchResult(value: unknown): TabFetchResult {
  if (typeof value !== "object" || value === null) {
    throw new Error("The tab returned an invalid fetch result");
  }
  const record = value as Record<string, unknown>;
  const status = record.status;
  const body = record.body;
  if (typeof status !== "number" || typeof body !== "string") {
    throw new Error("The tab returned an invalid fetch result");
  }
  return {
    status,
    headers: sanitizeHeaders(record.headers),
    body,
  };
}
