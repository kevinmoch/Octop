import { useState, useRef, useCallback, useEffect } from "react";
import { getAuthToken } from "../api/request";
import { resolveWsUrl } from "../utils/desktopWsUrl";
import { normalizeUrl } from "../utils/normalizeUrl";
import {
  closeBrowserTabOptimistic,
  markBrowserTabActive,
  mergeBrowserTabsStable,
} from "../utils/browserTabs";

export type BrowserStreamState =
  | "idle"
  | "connecting"
  | "browser_started"
  | "streaming"
  | "stopped"
  | "error";

export interface BrowserTab {
  id: number | string;
  url: string;
  title: string;
  active: boolean;
}

interface BrowserStreamCallbacks {
  onFrame: (base64Data: string) => void;
  onStatusChange?: (status: BrowserStreamState) => void;
  onError?: (message: string) => void;
}

interface ConnectOptions {
  /** Harness profile for the current user. The WebSocket also binds from JWT. */
  sessionId?: string | null;
  /** When set, screencast is relayed to the peer Octop via Bridge. */
  bridgeConnectionId?: string | null;
  /** Obtain a one-shot ticket, renewing an expired WebSkill session if needed. */
  issueTicket?: () => Promise<{ ticket: string }>;
}

function buildWsUrl(
  width: number,
  height: number,
  bridgeConnectionId?: string | null,
  ticket?: string,
): string {
  const token = ticket ? "" : getAuthToken();
  const path = bridgeConnectionId
    ? `/bridge/connections/${encodeURIComponent(
        bridgeConnectionId,
      )}/browser-stream/ws`
    : "/browser-stream/ws";
  const base = resolveWsUrl(`/api${path}`);
  const params = new URLSearchParams({
    width: String(width),
    height: String(height),
  });
  if (ticket) params.set("ticket", ticket);
  else if (token) params.set("token", token);
  return `${base}?${params.toString()}`;
}

/**
 * Manages a remote browser streaming WebSocket session.
 *
 * Handles connection lifecycle, frame reception, user input forwarding,
 * and multi-tab management.
 */
/** Lightweight session info pushed by the backend on state changes. */
export interface StreamSessionInfo {
  session_id: string;
  state: string;
  control_owner: "agent" | "user";
  current_url: string;
  conversation_id: string;
  channel_source: string;
}

export function useBrowserStream() {
  const wsRef = useRef<WebSocket | null>(null);
  const connectionGenerationRef = useRef(0);
  const callbacksRef = useRef<BrowserStreamCallbacks | null>(null);
  const statusRef = useRef<BrowserStreamState>("idle");
  const reconnectRef = useRef<(() => void) | null>(null);
  const reconnectRequestedRef = useRef(false);
  const pendingTabSwitchRef = useRef<{
    tabId: number | string;
    url?: string;
    title?: string;
  } | null>(null);
  const [status, setStatus] = useState<BrowserStreamState>("idle");
  const [currentUrl, setCurrentUrl] = useState("");
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [sessionInfo, setSessionInfo] = useState<StreamSessionInfo | null>(
    null,
  );

  const updateStatus = useCallback((s: BrowserStreamState) => {
    statusRef.current = s;
    setStatus(s);
    callbacksRef.current?.onStatusChange?.(s);
  }, []);

  /** Connect WebSocket and start the browser. */
  const connect = useCallback(
    (
      url: string,
      width: number,
      height: number,
      callbacks: BrowserStreamCallbacks,
      options: ConnectOptions = {},
    ) => {
      callbacksRef.current = callbacks;
      reconnectRef.current = () => {
        connect(url, width, height, callbacks, options);
      };
      updateStatus("connecting");

      // Close any existing connection
      if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) {
        wsRef.current.close();
      }

      const generation = ++connectionGenerationRef.current;
      void (async () => {
        let ticket: string | undefined;
        if (options.issueTicket) {
          ticket = (await options.issueTicket()).ticket;
          if (generation !== connectionGenerationRef.current) return;
        }
        if (generation !== connectionGenerationRef.current) return;
        const wsUrl = buildWsUrl(
          width,
          height,
          options.bridgeConnectionId,
          ticket,
        );
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          reconnectRequestedRef.current = false;
          const startMsg: Record<string, unknown> = {
            type: "start",
            url,
            width,
            height,
            reuse_session: true,
          };
          if (options.sessionId) {
            startMsg.session_id = options.sessionId;
          }
          ws.send(JSON.stringify(startMsg));
          setCurrentUrl(url);
        };

        ws.onmessage = (ev) => {
          if (generation !== connectionGenerationRef.current) return;
          try {
            const msg = JSON.parse(ev.data as string) as {
              type: string;
              data?: string;
              status?: string;
              message?: string;
              tabs?: BrowserTab[];
            };

            if (msg.type === "frame" && msg.data) {
              callbacksRef.current?.onFrame(msg.data);
            } else if (msg.type === "status" && msg.status) {
              updateStatus(msg.status as BrowserStreamState);
            } else if (msg.type === "tabs" && msg.tabs) {
              setTabs((prevTabs) =>
                mergeBrowserTabsStable(prevTabs, msg.tabs ?? []),
              );
              const activeTab = msg.tabs.find((t) => t.active);
              if (activeTab) setCurrentUrl(activeTab.url);
              // A tab switch requested while the stream was down is replayed
              // after the new connection has received its first tab snapshot.
              const pending = pendingTabSwitchRef.current;
              if (pending !== null && ws.readyState === WebSocket.OPEN) {
                pendingTabSwitchRef.current = null;
                ws.send(
                  JSON.stringify({
                    type: "tab_switch",
                    tab_id: pending.tabId,
                    ...(pending.url ? { tab_url: pending.url } : {}),
                    ...(pending.title ? { tab_title: pending.title } : {}),
                  }),
                );
                setTabs((prevTabs) =>
                  markBrowserTabActive(prevTabs, pending.tabId),
                );
              }
            } else if (msg.type === "error") {
              console.error("[BrowserStream] Server error:", msg.message);
              callbacksRef.current?.onError?.(msg.message ?? "Unknown error");
              updateStatus("error");
            } else if (msg.type === "session_update") {
              // Real-time session state pushed by the backend
              const info: StreamSessionInfo = {
                session_id:
                  ((msg as Record<string, unknown>).session_id as string) ?? "",
                state: ((msg as Record<string, unknown>).state as string) ?? "",
                control_owner:
                  ((msg as Record<string, unknown>).control_owner as
                    | "agent"
                    | "user") ?? "agent",
                current_url:
                  ((msg as Record<string, unknown>).current_url as string) ??
                  "",
                conversation_id:
                  ((msg as Record<string, unknown>)
                    .conversation_id as string) ?? "",
                channel_source:
                  ((msg as Record<string, unknown>).channel_source as string) ??
                  "dashboard",
              };
              setSessionInfo(info);
            }
          } catch {
            // Non-JSON frames — ignore
          }
        };

        ws.onerror = () => {
          if (generation === connectionGenerationRef.current)
            updateStatus("error");
        };

        ws.onclose = () => {
          if (generation !== connectionGenerationRef.current) return;
          reconnectRequestedRef.current = false;
          if (
            statusRef.current !== "stopped" &&
            statusRef.current !== "idle" &&
            statusRef.current !== "error"
          ) {
            updateStatus("stopped");
          }
          // Don't clear tabs on close — this preserves the tab bar state
          // during auto-reconnect so it doesn't flash empty.
        };

        wsRef.current = ws;
      })().catch((error: unknown) => {
        if (generation !== connectionGenerationRef.current) return;
        reconnectRequestedRef.current = false;
        callbacksRef.current?.onError?.(
          error instanceof Error ? error.message : "Browser connection failed",
        );
        updateStatus("error");
      });
    },
    [updateStatus],
  );

  /** Send a user input event to the remote browser. */
  const sendEvent = useCallback((event: Record<string, unknown>): boolean => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    wsRef.current.send(JSON.stringify(event));
    return true;
  }, []);

  /** Navigate to a new URL. */
  const navigate = useCallback(
    (url: string) => {
      const target = normalizeUrl(url);
      if (!target) return;
      sendEvent({ type: "navigate", url: target });
      setCurrentUrl(target);
    },
    [sendEvent],
  );

  /** Switch to a tab by id, queuing the switch across a reconnect. */
  const switchTab = useCallback(
    (tabId: number | string) => {
      // Include the visible URL/title as a fallback: after a browser restart the
      // old target id may be stale even though the tab label is still rendered.
      // The server can then resolve the current target by identity.
      const tab = tabs.find((item) => String(item.id) === String(tabId));
      const event = {
        type: "tab_switch",
        tab_id: tabId,
        ...(tab?.url ? { tab_url: tab.url } : {}),
        ...(tab?.title ? { tab_title: tab.title } : {}),
      };
      if (sendEvent(event)) {
        pendingTabSwitchRef.current = null;
        setTabs((prevTabs) => markBrowserTabActive(prevTabs, tabId));
        return;
      }

      // The stream is down (network interruption or a reconnect window).
      // Keep the user's intended target and replay it once the first tab
      // snapshot arrives on the new socket. Do not open a second reconnect
      // while the existing one is still connecting.
      pendingTabSwitchRef.current = {
        tabId,
        ...(tab?.url ? { url: tab.url } : {}),
        ...(tab?.title ? { title: tab.title } : {}),
      };
      setTabs((prevTabs) => markBrowserTabActive(prevTabs, tabId));
      if (
        statusRef.current !== "connecting" &&
        !reconnectRequestedRef.current
      ) {
        reconnectRequestedRef.current = true;
        reconnectRef.current?.();
      }
    },
    [sendEvent, tabs],
  );

  /** Close a tab by id. */
  const closeTab = useCallback(
    (tabId: number | string) => {
      if (sendEvent({ type: "tab_close", tab_id: tabId })) {
        setTabs((prevTabs) => closeBrowserTabOptimistic(prevTabs, tabId));
      }
    },
    [sendEvent],
  );

  /** Open a new blank tab. */
  const newTab = useCallback(() => {
    sendEvent({ type: "tab_new" });
  }, [sendEvent]);

  /** Stop the remote browser. */
  const stop = useCallback(() => {
    sendEvent({ type: "stop" });
    updateStatus("stopped");
  }, [sendEvent, updateStatus]);

  /** Disconnect WebSocket completely. */
  const disconnect = useCallback(() => {
    connectionGenerationRef.current += 1;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    }
    pendingTabSwitchRef.current = null;
    reconnectRequestedRef.current = false;
    updateStatus("idle");
    setTabs([]);
  }, [updateStatus]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          // ignore
        }
      }
    };
  }, []);

  return {
    status,
    currentUrl,
    tabs,
    sessionInfo,
    connect,
    sendEvent,
    navigate,
    switchTab,
    closeTab,
    newTab,
    stop,
    disconnect,
  };
}
