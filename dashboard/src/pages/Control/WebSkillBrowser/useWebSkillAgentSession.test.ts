// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { createSession, deleteSession, issueTicket } = vi.hoisted(() => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  issueTicket: vi.fn(),
}));

vi.mock("../../../api/modules/browserAgent", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("../../../api/modules/browserAgent")
  >()),
  browserAgentApi: { createSession, deleteSession, issueTicket },
}));

import { useWebSkillAgentSession } from "./useWebSkillAgentSession";

function session(sessionId: string) {
  return { sessionId, active: true, capabilities: {} };
}

function ticket(value: string) {
  return { ticket: value, expiresIn: 30 };
}

describe("useWebSkillAgentSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deleteSession.mockResolvedValue({ ok: true });
  });

  it.each([404, 410])(
    "renews a cached session and retries once after HTTP %i",
    async (status) => {
      createSession
        .mockResolvedValueOnce(session("stale"))
        .mockResolvedValueOnce(session("fresh"));
      issueTicket
        .mockResolvedValueOnce(ticket("initial"))
        .mockRejectedValueOnce(
          new Error(`Request failed: ${status} Missing - session expired`),
        )
        .mockResolvedValueOnce(ticket("recovered"));
      const { result } = renderHook(() => useWebSkillAgentSession(true, 1));

      expect(await act(() => result.current.issueTicket())).toEqual(
        ticket("initial"),
      );
      expect(await act(() => result.current.issueTicket())).toEqual(
        ticket("recovered"),
      );

      expect(createSession).toHaveBeenCalledTimes(2);
      expect(issueTicket.mock.calls.map(([id]) => id)).toEqual([
        "stale",
        "stale",
        "fresh",
      ]);
      expect(result.current.sessionId).toBe("fresh");
    },
  );

  it("does not renew after a non-missing-session error", async () => {
    createSession.mockResolvedValue(session("current"));
    issueTicket
      .mockResolvedValueOnce(ticket("initial"))
      .mockRejectedValueOnce(new Error("Request failed: 500 Internal Error"));
    const { result } = renderHook(() => useWebSkillAgentSession(true, 1));
    await act(() => result.current.issueTicket());

    await expect(act(() => result.current.issueTicket())).rejects.toThrow(
      "500",
    );
    expect(createSession).toHaveBeenCalledTimes(1);
    expect(result.current.sessionId).toBe("current");
  });

  it("shares an in-flight create between concurrent ticket requests", async () => {
    createSession.mockResolvedValue(session("shared"));
    issueTicket.mockImplementation(async (id: string) =>
      ticket(`${id}-ticket`),
    );
    const { result } = renderHook(() => useWebSkillAgentSession(true, 1));

    const tickets = await act(() =>
      Promise.all([result.current.issueTicket(), result.current.issueTicket()]),
    );

    expect(tickets.map((item) => item.ticket)).toEqual([
      "shared-ticket",
      "shared-ticket",
    ]);
    expect(createSession).toHaveBeenCalledTimes(1);
  });
});
