import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Spin } from "antd";
import { useTranslation } from "react-i18next";
import AssistantFrame, {
  type AssistantPlacement,
} from "./AssistantFrame/AssistantFrame";
import {
  browserAgentApi,
  isBrowserAgentSessionMissing,
  type PageActionRequest,
} from "../api/modules/browserAgent";
import { browserApi } from "../api/modules/browser";
import { useTheme } from "../context/ThemeContext";
import { showApiError } from "../utils/showApiToast";

const CHANNEL = "octop-webskill-p1";
type HostRequest = {
  channel: string;
  type: string;
  id: string;
  api:
    | "ticket"
    | "grant"
    | "handoff"
    | "skill-draft"
    | "replay"
    | "recording-delete";
  sessionId: string;
  payload?: unknown;
};

function isRequest(value: unknown): value is HostRequest {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return false;
  const data = value as Record<string, unknown>;
  return (
    data.channel === CHANNEL &&
    data.type === "request" &&
    typeof data.id === "string" &&
    data.id.length <= 128 &&
    typeof data.sessionId === "string" &&
    (data.api === "ticket" ||
      data.api === "grant" ||
      data.api === "handoff" ||
      data.api === "skill-draft" ||
      data.api === "replay" ||
      data.api === "recording-delete")
  );
}

export default function WebSkillFrame({
  sessionId,
  userId,
  browserProfile,
  browserActive,
  controlOwner,
  hidden,
  placement,
  placementLocked,
  onPlacementChange,
  onSessionExpired,
  onClose,
}: {
  sessionId: string | null;
  userId: number;
  browserProfile: string | null;
  browserActive: boolean;
  controlOwner: "agent" | "user";
  hidden: boolean;
  placement: AssistantPlacement;
  placementLocked: boolean;
  onPlacementChange: (placement: AssistantPlacement) => void;
  onSessionExpired: (expectedSessionId: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { mode } = useTheme();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const locale = i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";
  const frameUrl = new URL(
    `${import.meta.env.BASE_URL}webskill/`,
    window.location.origin,
  ).href;
  const secure =
    window.isSecureContext &&
    Boolean(globalThis.crypto?.subtle) &&
    typeof navigator.storage?.getDirectory === "function";
  const send = useCallback((message: Record<string, unknown>) => {
    frameRef.current?.contentWindow?.postMessage(
      { channel: CHANNEL, ...message },
      window.location.origin,
    );
  }, []);

  useEffect(() => {
    const listener = (event: MessageEvent<unknown>) => {
      if (
        event.origin !== window.location.origin ||
        event.source !== frameRef.current?.contentWindow
      )
        return;
      const data = event.data;
      if (typeof data !== "object" || data === null) return;
      const message = data as Record<string, unknown>;
      if (message.channel !== CHANNEL) return;
      if (message.type === "ready" && sessionId) {
        send({ type: "init", sessionId, userId, locale, theme: mode });
        setReady(true);
        return;
      }
      if (!sessionId || !isRequest(data) || data.sessionId !== sessionId)
        return;
      const respond = (result: Record<string, unknown>) =>
        send({ type: "response", id: data.id, ...result });
      void (async () => {
        try {
          if (data.api === "ticket") {
            respond({
              ok: true,
              result: await browserAgentApi.issueTicket(sessionId),
            });
          } else if (data.api === "grant") {
            const payload = data.payload;
            if (
              typeof payload !== "object" ||
              payload === null ||
              !("request" in payload)
            )
              throw new Error("Invalid action grant request");
            respond({
              ok: true,
              result: await browserAgentApi.grant(
                sessionId,
                payload.request as PageActionRequest,
              ),
            });
          } else if (data.api === "skill-draft") {
            const payload = data.payload;
            if (
              payload !== undefined &&
              (typeof payload !== "object" ||
                payload === null ||
                Array.isArray(payload) ||
                Object.keys(payload).some((key) => key !== "recordingId") ||
                ("recordingId" in payload &&
                  (typeof payload.recordingId !== "string" ||
                    !/^[A-Za-z0-9_-]{1,128}$/.test(payload.recordingId))))
            )
              throw new Error("Invalid recording skill request");
            const recordingId =
              typeof payload === "object" &&
              payload !== null &&
              "recordingId" in payload &&
              typeof payload.recordingId === "string"
                ? payload.recordingId
                : undefined;
            respond({
              ok: true,
              result: await browserAgentApi.skillDraft(sessionId, recordingId),
            });
          } else if (data.api === "replay") {
            const payload = data.payload;
            if (
              payload !== undefined &&
              (typeof payload !== "object" ||
                payload === null ||
                Array.isArray(payload) ||
                Object.keys(payload).some((key) => key !== "recordingId") ||
                ("recordingId" in payload &&
                  (typeof payload.recordingId !== "string" ||
                    !/^[A-Za-z0-9_-]{1,128}$/.test(payload.recordingId))))
            )
              throw new Error("Invalid replay request");
            const requestedId =
              typeof payload === "object" &&
              payload !== null &&
              "recordingId" in payload &&
              typeof payload.recordingId === "string"
                ? payload.recordingId
                : undefined;
            const recordingId =
              requestedId ??
              (await browserApi.recordReplayStatus()).latestRecordingId;
            if (!recordingId)
              throw new Error("No stopped recording is available");
            respond({
              ok: true,
              result: await browserAgentApi.replay(sessionId, recordingId),
            });
          } else if (data.api === "recording-delete") {
            const payload = data.payload;
            if (
              payload !== undefined &&
              (typeof payload !== "object" ||
                payload === null ||
                Array.isArray(payload) ||
                Object.keys(payload).some((key) => key !== "recordingId") ||
                ("recordingId" in payload &&
                  (typeof payload.recordingId !== "string" ||
                    !/^[A-Za-z0-9_-]{1,128}$/.test(payload.recordingId))))
            )
              throw new Error("Invalid recording deletion request");
            const requestedId =
              typeof payload === "object" &&
              payload !== null &&
              "recordingId" in payload &&
              typeof payload.recordingId === "string"
                ? payload.recordingId
                : undefined;
            const recordingId =
              requestedId ??
              (await browserApi.recordReplayStatus()).latestRecordingId;
            if (!recordingId)
              throw new Error("No stopped recording is available");
            respond({
              ok: true,
              result: await browserAgentApi.deleteRecording(recordingId),
            });
          } else {
            const payload = data.payload;
            const target =
              typeof payload === "object" &&
              payload !== null &&
              "target" in payload
                ? payload.target
                : undefined;
            if (target !== "agent" && target !== "user")
              throw new Error("Invalid handoff target");
            if (!browserProfile)
              throw new Error("The browser profile is unavailable");
            respond({
              ok: true,
              result: await browserApi.handoff(
                browserProfile,
                target,
                "WebSkill assistant panel",
              ),
            });
          }
        } catch (error) {
          if (data.api === "ticket" && isBrowserAgentSessionMissing(error)) {
            await onSessionExpired(data.sessionId).catch(() => undefined);
          }
          respond({
            ok: false,
            error:
              error instanceof Error
                ? error.message.slice(0, 300)
                : "Request failed",
          });
        }
      })();
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [browserProfile, locale, mode, onSessionExpired, send, sessionId, userId]);

  useEffect(() => {
    if (!ready) return;
    send({ type: "appearance", locale, theme: mode });
  }, [locale, mode, ready, send]);

  if (!secure) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("webskill.insecureTitle")}
        description={t("webskill.insecureDescription")}
      />
    );
  }

  return (
    <AssistantFrame
      placement={placement}
      onPlacementChange={onPlacementChange}
      placementKey="octop:webskill-browser:ai-placement"
      storageKey="octop:webskill-browser:ai-panel"
      placementLocked={placementLocked}
      placements={["right", "bottom", "float"]}
      hidden={hidden}
      title="WebSkill"
      onClose={onClose}
      headerExtra={
        <Button
          size="small"
          type="link"
          disabled={!sessionId || !browserProfile || handoffBusy}
          onClick={() => {
            const profile = browserProfile;
            if (!sessionId || !profile) return;
            setHandoffBusy(true);
            void browserApi
              .handoff(
                profile,
                controlOwner === "user" ? "agent" : "user",
                "WebSkill explicit handoff",
              )
              .catch((error) =>
                showApiError(error, t("webskill.handoffFailed"), t),
              )
              .finally(() => setHandoffBusy(false));
          }}
        >
          {controlOwner === "user"
            ? t("webskill.returnControl")
            : t("webskill.takeControl")}
        </Button>
      }
      bodyClassName="webskill-frame-body"
    >
      <div
        style={{
          height: "100%",
          minHeight: 0,
          position: "relative",
          background: "var(--fn-bg-primary)",
        }}
      >
        {(!sessionId || !ready) && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              zIndex: 1,
              display: "grid",
              placeItems: "center",
              background: "var(--fn-bg-primary)",
            }}
          >
            <Spin
              tip={
                sessionId ? t("webskill.loading") : t("webskill.startRequired")
              }
            />
          </div>
        )}
        {sessionId && browserActive && (
          <iframe
            key={sessionId}
            ref={frameRef}
            title="WebSkill"
            src={frameUrl}
            sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-downloads"
            style={{
              display: "block",
              width: "100%",
              height: "100%",
              border: 0,
            }}
          />
        )}
      </div>
    </AssistantFrame>
  );
}
