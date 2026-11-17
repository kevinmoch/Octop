import { request } from "../request";

export interface BrowserAgentSession {
  sessionId: string;
  active: boolean;
  capabilities: {
    frames: boolean;
    oopif: boolean;
    imageCapture: boolean;
    targetWorkset: boolean;
    actions: string[];
  };
  startUrl?: string;
  controlOwner?: "agent" | "user";
}

export interface PageActionRequest {
  ref: string;
  action: string;
  value?: string;
}

export function isBrowserAgentSessionMissing(error: unknown): boolean {
  return (
    error instanceof Error &&
    /^Request failed: (?:404|410)\b/.test(error.message)
  );
}

export const browserAgentApi = {
  createSession: (startUrl = "") =>
    request<BrowserAgentSession>("/browser-agent/sessions", {
      method: "POST",
      body: JSON.stringify({ startUrl }),
    }),
  getSession: (sessionId: string) =>
    request<BrowserAgentSession>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}`,
    ),
  deleteSession: (sessionId: string) =>
    request<{ ok: boolean }>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    ),
  issueTicket: (sessionId: string) =>
    request<{ ticket: string; expiresIn: number }>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/tickets`,
      { method: "POST" },
    ),
  grant: (sessionId: string, action: PageActionRequest) =>
    request<{ grant: string; expiresIn: number }>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/grants`,
      {
        method: "POST",
        body: JSON.stringify({ request: action }),
      },
    ),
};
