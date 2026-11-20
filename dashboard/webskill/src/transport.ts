import { WebSkillError } from "@webskill/sdk";
import { createFrameRouter } from "@webskill/sdk/browser";
import type {
  FrameDelivery,
  FrameRouter,
  PageAgentReply,
  PageAgentRequest,
  PageAgentTransport,
} from "@webskill/sdk/browser";
import { OctopHostBridge, isRecord } from "./bridge";

const REPLY_CODES = new Set([
  "PERCEPTION_FAILED",
  "PERCEPTION_SUBTREE_UNSUPPORTED",
  "PERCEPTION_SUBTREE_OUT_OF_SCOPE",
  "PERCEPTION_SUBTREE_UNKNOWN_REF",
  "PAGE_ACTION_STALE_REF",
  "PAGE_ACTION_OUT_OF_SCOPE",
  "PAGE_TAB_UNKNOWN",
  "PAGE_TAB_EXPIRED",
  "WRITE_LOCKED",
  "TOOL_EXECUTION_FAILED",
]);

function isDocument(value: unknown): boolean {
  return isRecord(value) && typeof value.key === "string";
}

function parseReply(value: unknown): PageAgentReply | undefined {
  if (!isRecord(value)) return;
  if (value.type === "error" && typeof value.message === "string") {
    const code =
      typeof value.code === "string" && REPLY_CODES.has(value.code)
        ? value.code
        : "TOOL_EXECUTION_FAILED";
    return { type: "error", code, message: value.message.slice(0, 1000) };
  }
  if (
    value.type === "perceive-result" &&
    isRecord(value.result) &&
    Array.isArray(value.result.nodes) &&
    Array.isArray(value.targets)
  ) {
    if (value.document !== undefined && !isDocument(value.document)) return;
    return value as unknown as PageAgentReply;
  }
  if (value.type === "capture-image-result" && isRecord(value.result)) {
    return value as unknown as PageAgentReply;
  }
  if (
    value.type === "execute-result" &&
    isRecord(value.outcome) &&
    typeof value.outcome.ok === "boolean" &&
    isRecord(value.outcome.target) &&
    typeof value.outcome.target.role === "string"
  ) {
    return value as unknown as PageAgentReply;
  }
  return;
}

export class OctopPageAgentTransport implements PageAgentTransport {
  private socket?: WebSocket;
  private opening?: Promise<WebSocket>;
  private queue: Promise<void> = Promise.resolve();
  private readonly pending = new Map<
    string,
    {
      resolve: (reply: PageAgentReply) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private disposed = false;

  private readonly router: FrameRouter;

  constructor(
    private readonly bridge: OctopHostBridge,
    private readonly actionScope: Record<string, unknown> = {
      include: ["body"],
    },
  ) {
    this.router = createFrameRouter<string>({
      dispatcher: {
        mainFrame: () => "self",
        send: (address, request) => this.sendWithFrame(address, request),
        resolveNestedFrame: (nested, context) => {
          const path = nested.frame;
          if (!path || context.visited.includes(path)) return undefined;
          return path;
        },
      },
    });
  }

  send(request: PageAgentRequest): Promise<PageAgentReply> {
    if (request.type === "perceive")
      return this.router.perceive({
        ...request,
        actionScope: this.actionScope,
      } as typeof request);
    if (request.type === "capture-image")
      return this.router.captureImage(request);
    return this.router.execute(request);
  }

  private async sendWithFrame(
    address: string,
    request: PageAgentRequest,
  ): Promise<FrameDelivery> {
    if (address === "self" || request.type !== "perceive") {
      return { reply: await this.sendDirect(request), documentUrl: "" };
    }
    const reply = await this.sendDirect({
      ...request,
      scope: {
        frames: [{ frame: address.split(" >>> "), include: ["body"] }],
      },
    });
    const page =
      reply.type === "perceive-result" && isRecord(reply.result.page)
        ? reply.result.page
        : undefined;
    return {
      reply,
      documentUrl: typeof page?.url === "string" ? page.url : "",
    };
  }

  private async sendDirect(request: PageAgentRequest): Promise<PageAgentReply> {
    const operation = this.queue.then(() => this.sendOnce(request));
    this.queue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }

  private async sendOnce(request: PageAgentRequest): Promise<PageAgentReply> {
    if (this.disposed)
      throw new WebSkillError(
        "TOOL_EXECUTION_FAILED",
        "The PageAgent transport is closed",
      );
    let grant: string | undefined;
    if (request.type === "execute" && request.request.action === "attach") {
      const selection = await this.bridge.call("attach-files", {});
      if (!isRecord(selection) || selection.cancelled === true) {
        throw new WebSkillError(
          "TOOL_EXECUTION_FAILED",
          "Attach was cancelled: no files were selected",
        );
      }
    }
    if (request.type === "execute") {
      const reply = await this.bridge.call("grant", {
        request: request.request,
      });
      if (!isRecord(reply) || typeof reply.grant !== "string")
        throw new WebSkillError(
          "PAGE_ACTION_OUT_OF_SCOPE",
          "The Octop host did not grant this confirmed action",
        );
      grant = reply.grant;
    }
    // Connect and grant first, but never retry a write after disconnect or timeout.
    const socket = await this.connect();
    const requestId = crypto.randomUUID();
    return new Promise<PageAgentReply>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        reject(
          new WebSkillError(
            "TOOL_EXECUTION_FAILED",
            "The PageAgent request timed out; it will not be replayed",
          ),
        );
      }, 15_000);
      this.pending.set(requestId, { resolve, reject, timer });
      socket.send(
        JSON.stringify({
          ...request,
          requestId,
          ...(grant !== undefined ? { grant } : {}),
        }),
      );
    });
  }

  private async connect(): Promise<WebSocket> {
    if (this.socket?.readyState === WebSocket.OPEN) return this.socket;
    if (this.opening) return this.opening;
    this.opening = (async () => {
      const issued = await this.bridge.call("ticket");
      if (!isRecord(issued) || typeof issued.ticket !== "string")
        throw new Error("The Octop host did not issue a PageAgent ticket");
      const url = new URL("/api/browser-agent/ws", window.location.origin);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.searchParams.set("ticket", issued.ticket);
      const socket = new WebSocket(url);
      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve();
        socket.onerror = () =>
          reject(new Error("The PageAgent WebSocket could not connect"));
      });
      socket.onmessage = (event) => this.onMessage(event);
      socket.onclose = () => this.onClose(socket);
      this.socket = socket;
      return socket;
    })();
    try {
      return await this.opening;
    } finally {
      this.opening = undefined;
    }
  }

  private onMessage(event: MessageEvent<unknown>): void {
    if (typeof event.data !== "string") return;
    let raw: unknown;
    try {
      raw = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!isRecord(raw) || typeof raw.requestId !== "string") return;
    const waiter = this.pending.get(raw.requestId);
    if (!waiter) return;
    const reply = parseReply(raw);
    this.pending.delete(raw.requestId);
    clearTimeout(waiter.timer);
    if (!reply) {
      waiter.reject(
        new WebSkillError(
          "TOOL_EXECUTION_FAILED",
          "The Octop PageAgent returned an invalid reply",
        ),
      );
      return;
    }
    waiter.resolve(reply);
  }

  private onClose(socket: WebSocket | undefined): void {
    if (this.socket === socket) this.socket = undefined;
    for (const waiter of this.pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(
        new WebSkillError(
          "TOOL_EXECUTION_FAILED",
          "The PageAgent connection closed; in-flight writes are not replayed",
        ),
      );
    }
    this.pending.clear();
  }

  dispose(): void {
    this.disposed = true;
    const socket = this.socket;
    this.socket = undefined;
    socket?.close();
    this.onClose(socket);
  }
}
