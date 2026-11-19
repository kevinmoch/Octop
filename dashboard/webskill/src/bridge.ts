export const HOST_CHANNEL = "octop-webskill-p1";
export type HostTheme = "light" | "dark";
export type HostLocale = "zh" | "en";

export interface HostInit {
  sessionId: string;
  userId: number;
  locale: HostLocale;
  theme: HostTheme;
}

export type HostApi =
  | "ticket"
  | "grant"
  | "handoff"
  | "skill-draft"
  | "replay"
  | "recording-delete"
  | "tabs-list"
  | "tabs-focus"
  | "tabs-close"
  | "tabs-admit"
  | "tabs-reset";

type HostRequest = {
  channel: typeof HOST_CHANNEL;
  type: "request";
  id: string;
  api: HostApi;
  sessionId: string;
  payload?: unknown;
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseInit(value: unknown): HostInit | undefined {
  if (
    !isRecord(value) ||
    value.channel !== HOST_CHANNEL ||
    value.type !== "init"
  )
    return;
  const { sessionId, userId, locale, theme } = value;
  if (
    typeof sessionId !== "string" ||
    sessionId.length === 0 ||
    sessionId.length > 128 ||
    !Number.isInteger(userId) ||
    (userId as number) < 1 ||
    (locale !== "zh" && locale !== "en") ||
    (theme !== "light" && theme !== "dark")
  )
    return;
  return { sessionId, userId: userId as number, locale, theme };
}

export function isHostRequest(value: unknown): value is HostRequest {
  if (
    !isRecord(value) ||
    value.channel !== HOST_CHANNEL ||
    value.type !== "request"
  )
    return false;
  if (
    typeof value.id !== "string" ||
    value.id.length === 0 ||
    value.id.length > 128
  )
    return false;
  if (
    value.api !== "ticket" &&
    value.api !== "grant" &&
    value.api !== "handoff" &&
    value.api !== "skill-draft" &&
    value.api !== "replay" &&
    value.api !== "recording-delete" &&
    value.api !== "tabs-list" &&
    value.api !== "tabs-focus" &&
    value.api !== "tabs-close" &&
    value.api !== "tabs-admit" &&
    value.api !== "tabs-reset"
  )
    return false;
  return typeof value.sessionId === "string" && value.sessionId.length <= 128;
}

export class OctopHostBridge {
  private readonly pending = new Map<
    string,
    {
      resolve: (value: unknown) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private initPromise: Promise<HostInit>;
  private initResolve!: (value: HostInit) => void;
  private initReject!: (error: Error) => void;
  private init?: HostInit;
  private appearance?: Pick<HostInit, "locale" | "theme">;
  private readonly appearanceListeners = new Set<
    (appearance: Pick<HostInit, "locale" | "theme">) => void
  >();
  private disposed = false;

  constructor() {
    this.initPromise = new Promise((resolve, reject) => {
      this.initResolve = resolve;
      this.initReject = reject;
    });
    window.addEventListener("message", this.onMessage);
    window.parent.postMessage(
      { channel: HOST_CHANNEL, type: "ready" },
      window.location.origin,
    );
  }

  private onMessage = (event: MessageEvent<unknown>): void => {
    if (
      event.origin !== window.location.origin ||
      event.source !== window.parent
    )
      return;
    const value = event.data;
    if (!isRecord(value) || value.channel !== HOST_CHANNEL) return;
    if (value.type === "init") {
      if (this.init !== undefined) return;
      const init = parseInit(value);
      if (init) {
        this.init = init;
        this.initResolve(init);
      } else {
        this.initReject(
          new Error("The Octop host sent an invalid initialization message"),
        );
      }
      return;
    }
    if (
      value.type === "appearance" &&
      (value.locale === "zh" || value.locale === "en") &&
      (value.theme === "light" || value.theme === "dark")
    ) {
      this.appearance = { locale: value.locale, theme: value.theme };
      for (const listener of this.appearanceListeners)
        listener(this.appearance);
      return;
    }
    if (value.type !== "response" || typeof value.id !== "string") return;
    const waiter = this.pending.get(value.id);
    if (!waiter) return;
    this.pending.delete(value.id);
    clearTimeout(waiter.timer);
    if (value.ok === true) waiter.resolve(value.result);
    else
      waiter.reject(
        new Error(
          typeof value.error === "string"
            ? value.error.slice(0, 300)
            : "The Octop request failed",
        ),
      );
  };

  async ready(): Promise<HostInit> {
    return this.initPromise;
  }

  onAppearance(
    listener: (appearance: Pick<HostInit, "locale" | "theme">) => void,
  ): () => void {
    this.appearanceListeners.add(listener);
    if (this.appearance) listener(this.appearance);
    return () => this.appearanceListeners.delete(listener);
  }

  async call(api: HostApi, payload?: unknown): Promise<unknown> {
    const init = await this.ready();
    if (this.disposed) throw new Error("The Octop host connection is closed");
    const id = crypto.randomUUID();
    const result = new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("The Octop host request timed out"));
      }, 10_000);
      this.pending.set(id, { resolve, reject, timer });
    });
    const message: HostRequest = {
      channel: HOST_CHANNEL,
      type: "request",
      id,
      api,
      sessionId: init.sessionId,
      ...(payload !== undefined ? { payload } : {}),
    };
    window.parent.postMessage(message, window.location.origin);
    return result;
  }

  dispose(): void {
    this.disposed = true;
    window.removeEventListener("message", this.onMessage);
    for (const waiter of this.pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error("The Octop host connection is closed"));
    }
    this.pending.clear();
    this.appearanceListeners.clear();
  }
}
