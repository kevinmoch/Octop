// @vitest-environment jsdom
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWebSkillAgentSession } from "../pages/Control/WebSkillBrowser/useWebSkillAgentSession";
import WebSkillFrame from "./WebSkillFrame";

const { createSession, deleteSession, grant, issueTicket } = vi.hoisted(() => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  grant: vi.fn(),
  issueTicket: vi.fn(),
}));

vi.mock("../api/modules/browserAgent", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/modules/browserAgent")>()),
  browserAgentApi: { createSession, deleteSession, grant, issueTicket },
}));

vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ mode: "light" }),
}));

vi.mock("./AssistantFrame/AssistantFrame", () => ({
  default: ({
    children,
    headerExtra,
  }: {
    children: ReactNode;
    headerExtra?: ReactNode;
  }) => (
    <div>
      {headerExtra}
      {children}
    </div>
  ),
}));

function session(sessionId: string) {
  return { sessionId, active: true, capabilities: {} };
}

function ticket(value: string) {
  return { ticket: value, expiresIn: 30 };
}

describe("WebSkillFrame session recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deleteSession.mockResolvedValue({ ok: true });
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("crypto", { subtle: {} });
    vi.stubGlobal("navigator", {
      storage: { getDirectory: vi.fn() },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not replace the new session when an old iframe ticket fails late", async () => {
    let agent: ReturnType<typeof useWebSkillAgentSession> | undefined;
    const renewSpy = vi.fn(async (expectedSessionId: string) => {
      if (!agent) throw new Error("Missing test agent");
      await agent.renewSession(expectedSessionId);
    });

    function Harness() {
      const current = useWebSkillAgentSession(true, 1);
      agent = current;
      return (
        <WebSkillFrame
          sessionId={current.sessionId}
          userId={1}
          browserProfile="user-1"
          browserActive
          controlOwner="agent"
          hidden={false}
          placement="right"
          placementLocked={false}
          onPlacementChange={vi.fn()}
          onSessionExpired={renewSpy}
          onClose={vi.fn()}
        />
      );
    }

    createSession
      .mockResolvedValueOnce(session("stale"))
      .mockResolvedValueOnce(session("fresh"));
    issueTicket.mockResolvedValueOnce(ticket("initial"));
    const { container } = render(<Harness />);
    await act(() => agent!.issueTicket());

    const oldFrame = container.querySelector("iframe");
    expect(oldFrame).not.toBeNull();
    let rejectOld: ((error: Error) => void) | undefined;
    issueTicket.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectOld = reject;
        }),
    );
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          source: oldFrame!.contentWindow,
          data: {
            channel: "octop-webskill-p1",
            type: "request",
            id: "old-ticket",
            api: "ticket",
            sessionId: "stale",
          },
        }),
      );
      await vi.waitFor(() => {
        expect(issueTicket).toHaveBeenCalledWith("stale");
        expect(rejectOld).toBeTypeOf("function");
      });
    });

    await act(() => agent!.renewSession("stale"));
    expect(agent!.sessionId).toBe("fresh");
    expect(createSession).toHaveBeenCalledTimes(2);
    const newFrame = container.querySelector("iframe");
    expect(newFrame).not.toBe(oldFrame);
    vi.spyOn(newFrame!.contentWindow!, "postMessage").mockImplementation(
      () => undefined,
    );

    await act(async () => {
      rejectOld?.(new Error("Request failed: 404 Missing"));
      await Promise.resolve();
    });

    await vi.waitFor(() => expect(renewSpy).toHaveBeenCalledWith("stale"));
    expect(createSession).toHaveBeenCalledTimes(2);
    expect(agent!.sessionId).toBe("fresh");
  });
});
