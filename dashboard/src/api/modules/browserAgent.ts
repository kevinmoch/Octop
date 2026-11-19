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

export interface BrowserAgentTab {
  handle: string;
  title?: string;
  url?: string;
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
  skillDraft: (sessionId: string, recordingId?: string) =>
    request<unknown>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/skill-drafts`,
      {
        method: "POST",
        body: JSON.stringify({ ...(recordingId ? { recordingId } : {}) }),
      },
    ),
  replay: (sessionId: string, recordingId: string) =>
    request<unknown>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/replays`,
      {
        method: "POST",
        body: JSON.stringify({ recordingId }),
      },
    ),
  deleteRecording: (recordingId: string) =>
    request<{ deleted: boolean; recordingId: string }>(
      `/browser-agent/recordings/${encodeURIComponent(recordingId)}`,
      { method: "DELETE" },
    ),
  listTabs: (sessionId: string) =>
    request<{ tabs: BrowserAgentTab[] }>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/tabs`,
    ),
  focusTab: (sessionId: string, handle: string) =>
    request<{ focused: string }>(
      `/browser-agent/sessions/${encodeURIComponent(
        sessionId,
      )}/tabs/${encodeURIComponent(handle)}/focus`,
      { method: "POST" },
    ),
  closeTab: (sessionId: string, handle: string) =>
    request<{ closed: string; focused?: string }>(
      `/browser-agent/sessions/${encodeURIComponent(
        sessionId,
      )}/tabs/${encodeURIComponent(handle)}`,
      { method: "DELETE" },
    ),
  admitTab: (sessionId: string, opened: { title?: string; url?: string }) =>
    request<{ handle: string }>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/tabs/admit`,
      { method: "POST", body: JSON.stringify(opened) },
    ),
  resetTabs: (sessionId: string) =>
    request<{ ok: boolean }>(
      `/browser-agent/sessions/${encodeURIComponent(sessionId)}/tabs/reset`,
      { method: "POST" },
    ),
};
