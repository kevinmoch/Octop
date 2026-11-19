// @vitest-environment jsdom
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWebSkillAgentSession } from "../pages/Control/WebSkillBrowser/useWebSkillAgentSession";
import WebSkillFrame from "./WebSkillFrame";

const {
  createSession,
  deleteSession,
  grant,
  issueTicket,
  skillDraft,
  replay,
  deleteRecording,
  recordReplayStatus,
} = vi.hoisted(() => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  grant: vi.fn(),
  issueTicket: vi.fn(),
  skillDraft: vi.fn(),
  replay: vi.fn(),
  deleteRecording: vi.fn(),
  recordReplayStatus: vi.fn(),
}));

vi.mock("../api/modules/browserAgent", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/modules/browserAgent")>()),
  browserAgentApi: {
    createSession,
    deleteSession,
    grant,
    issueTicket,
    skillDraft,
    replay,
    deleteRecording,
  },
}));

vi.mock("../api/modules/browser", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/modules/browser")>()),
  browserApi: {
    ...(await importOriginal<typeof import("../api/modules/browser")>())
      .browserApi,
    recordReplayStatus,
  },
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

describe("WebSkillFrame recording skill requests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("crypto", { subtle: {} });
    vi.stubGlobal("navigator", {
      storage: { getDirectory: vi.fn() },
    });
    skillDraft.mockResolvedValue({ draft: { name: "recorded-example" } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the session boundary and validates the recording id", async () => {
    const { container } = render(
      <WebSkillFrame
        sessionId="session-1"
        userId={1}
        browserProfile="user-1"
        browserActive
        controlOwner="agent"
        hidden={false}
        placement="right"
        placementLocked={false}
        onPlacementChange={vi.fn()}
        onSessionExpired={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const frame = container.querySelector("iframe");
    const postMessage = vi.spyOn(frame!.contentWindow!, "postMessage");
    const send = async (
      id: string,
      requestSessionId: string,
      recordingId: string,
    ) => {
      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            source: frame!.contentWindow,
            data: {
              channel: "octop-webskill-p1",
              type: "request",
              id,
              api: "skill-draft",
              sessionId: requestSessionId,
              payload: { recordingId },
            },
          }),
        );
        await Promise.resolve();
      });
    };

    await send("wrong-session", "other", "rec_test");
    await send("invalid-id", "session-1", "../other");
    expect(skillDraft).not.toHaveBeenCalled();
    await send("valid", "session-1", "rec_test");
    await vi.waitFor(() =>
      expect(skillDraft).toHaveBeenCalledWith("session-1", "rec_test"),
    );
    expect(skillDraft).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "response",
        id: "valid",
        ok: true,
        result: { draft: { name: "recorded-example" } },
      }),
      window.location.origin,
    );
  });
});

describe("WebSkillFrame replay requests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("crypto", { subtle: {} });
    vi.stubGlobal("navigator", {
      storage: { getDirectory: vi.fn() },
    });
    recordReplayStatus.mockResolvedValue({ latestRecordingId: "rec_latest" });
    replay.mockResolvedValue({
      replayId: "replay_1",
      recordingId: "rec_latest",
      status: "passed",
      steps: [],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves only the current session and the current user's latest recording", async () => {
    const { container } = render(
      <WebSkillFrame
        sessionId="session-1"
        userId={1}
        browserProfile="user-1"
        browserActive
        controlOwner="agent"
        hidden={false}
        placement="right"
        placementLocked={false}
        onPlacementChange={vi.fn()}
        onSessionExpired={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const frame = container.querySelector("iframe");
    const postMessage = vi.spyOn(frame!.contentWindow!, "postMessage");
    const send = async (
      id: string,
      sessionId: string,
      recordingId?: string,
    ) => {
      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            source: frame!.contentWindow,
            data: {
              channel: "octop-webskill-p1",
              type: "request",
              id,
              api: "replay",
              sessionId,
              ...(recordingId ? { payload: { recordingId } } : {}),
            },
          }),
        );
        await Promise.resolve();
      });
    };

    await send("wrong", "other");
    expect(replay).not.toHaveBeenCalled();
    await send("latest", "session-1");
    await vi.waitFor(() =>
      expect(replay).toHaveBeenCalledWith("session-1", "rec_latest"),
    );
    await send("explicit", "session-1", "rec_given");
    await vi.waitFor(() =>
      expect(replay).toHaveBeenCalledWith("session-1", "rec_given"),
    );
    expect(recordReplayStatus).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ id: "explicit", ok: true }),
      window.location.origin,
    );
  });
});

describe("WebSkillFrame recording deletion", () => {
  it("resolves the current user's latest recording and rejects invalid ids", async () => {
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("crypto", { subtle: {} });
    vi.stubGlobal("navigator", { storage: { getDirectory: vi.fn() } });
    recordReplayStatus.mockResolvedValue({ latestRecordingId: "rec_latest" });
    deleteRecording.mockResolvedValue({
      deleted: true,
      recordingId: "rec_latest",
    });
    const { container } = render(
      <WebSkillFrame
        sessionId="session-1"
        userId={1}
        browserProfile="user-1"
        browserActive
        controlOwner="agent"
        hidden={false}
        placement="right"
        placementLocked={false}
        onPlacementChange={vi.fn()}
        onSessionExpired={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const frame = container.querySelector("iframe")!;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    const send = async (id: string, recordingId?: string) => {
      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            origin: window.location.origin,
            source: frame.contentWindow,
            data: {
              channel: "octop-webskill-p1",
              type: "request",
              id,
              api: "recording-delete",
              sessionId: "session-1",
              ...(recordingId ? { payload: { recordingId } } : {}),
            },
          }),
        );
        await Promise.resolve();
      });
    };
    await send("invalid", "../other-user");
    expect(deleteRecording).not.toHaveBeenCalled();
    await send("delete");
    await vi.waitFor(() =>
      expect(deleteRecording).toHaveBeenCalledWith("rec_latest"),
    );
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ id: "delete", ok: true }),
      window.location.origin,
    );
    vi.unstubAllGlobals();
  });
});
