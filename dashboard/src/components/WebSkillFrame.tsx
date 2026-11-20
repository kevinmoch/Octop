import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Spin } from "antd";
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
    | "recording-delete"
    | "tabs-list"
    | "tabs-focus"
    | "tabs-close"
    | "tabs-admit"
    | "tabs-reset"
    | "pdf-extract"
    | "downloads-list"
    | "downloads-read"
    | "linked-document";
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
      data.api === "recording-delete" ||
      data.api === "tabs-list" ||
      data.api === "tabs-focus" ||
      data.api === "tabs-close" ||
      data.api === "tabs-admit" ||
      data.api === "tabs-reset" ||
      data.api === "pdf-extract" ||
      data.api === "downloads-list" ||
      data.api === "downloads-read" ||
      data.api === "linked-document")
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
  const [frameLoaded, setFrameLoaded] = useState(false);
  const handoffRef = useRef(false);
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

  const requestAgentControl = useCallback(() => {
    if (!browserProfile || handoffRef.current) {
      return;
    }
    handoffRef.current = true;
    void browserApi
      .handoff(browserProfile, "agent", "WebSkill panel focus")
      .catch((error) => showApiError(error, t("webskill.handoffFailed"), t))
      .finally(() => {
        window.setTimeout(() => {
          handoffRef.current = false;
        }, 500);
      });
  }, [browserProfile, controlOwner, t]);

  useEffect(() => {
    if (!frameLoaded || !frameRef.current) return;
    const doc = frameRef.current.contentDocument;
    if (!doc) return;
    const onInteract = () => requestAgentControl();
    doc.addEventListener("pointerdown", onInteract, true);
    doc.addEventListener("focusin", onInteract, true);
    return () => {
      doc.removeEventListener("pointerdown", onInteract, true);
      doc.removeEventListener("focusin", onInteract, true);
    };
  }, [frameLoaded, requestAgentControl]);

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
      if (message.type === "interact") {
        requestAgentControl();
        return;
      }
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
          } else if (data.api === "linked-document") {
            const payload = data.payload;
            if (
              typeof payload !== "object" ||
              payload === null ||
              Array.isArray(payload) ||
              Object.keys(payload).some((key) => key !== "url") ||
              typeof (payload as Record<string, unknown>).url !== "string" ||
              ((payload as Record<string, unknown>).url as string).length > 2048
            )
              throw new Error("Invalid linked document URL");
            respond({
              ok: true,
              result: await browserAgentApi.readLinkedDocument(
                sessionId,
                (payload as Record<string, string>).url,
              ),
            });
          } else if (data.api === "downloads-list") {
            respond({
              ok: true,
              result: await browserAgentApi.listDownloads(sessionId),
            });
          } else if (data.api === "downloads-read") {
            const payload = data.payload;
            if (
              typeof payload !== "object" ||
              payload === null ||
              Array.isArray(payload) ||
              Object.keys(payload).some((key) => key !== "id") ||
              typeof (payload as Record<string, unknown>).id !== "string" ||
              !/^dl_[A-Za-z0-9_-]{20,80}$/.test(
                (payload as Record<string, unknown>).id as string,
              )
            )
              throw new Error("Invalid download id");
            respond({
              ok: true,
              result: await browserAgentApi.readDownload(
                sessionId,
                (payload as Record<string, string>).id,
              ),
            });
          } else if (data.api === "pdf-extract") {
            const payload = data.payload;
            if (
              typeof payload !== "object" ||
              payload === null ||
              Array.isArray(payload) ||
              Object.keys(payload).some((key) => key !== "data") ||
              typeof (payload as Record<string, unknown>).data !== "string" ||
              ((payload as Record<string, unknown>).data as string).length >
                28_000_000
            )
              throw new Error("Invalid PDF payload");
            respond({
              ok: true,
              result: await browserAgentApi.extractPdf(
                (payload as Record<string, string>).data,
              ),
            });
          } else if (data.api === "tabs-list") {
            respond({
              ok: true,
              result: await browserAgentApi.listTabs(sessionId),
            });
          } else if (data.api === "tabs-reset") {
            respond({
              ok: true,
              result: await browserAgentApi.resetTabs(sessionId),
            });
          } else if (data.api === "tabs-focus" || data.api === "tabs-close") {
            const payload = data.payload;
            if (
              typeof payload !== "object" ||
              payload === null ||
              Array.isArray(payload) ||
              typeof (payload as Record<string, unknown>).handle !== "string" ||
              !/^tab_[A-Za-z0-9_-]{20,80}$/.test(
                (payload as Record<string, unknown>).handle as string,
              )
            )
              throw new Error("Invalid page handle");
            const handle = (payload as Record<string, unknown>)
              .handle as string;
            respond({
              ok: true,
              result:
                data.api === "tabs-focus"
                  ? await browserAgentApi.focusTab(sessionId, handle)
                  : await browserAgentApi.closeTab(sessionId, handle),
            });
          } else if (data.api === "tabs-admit") {
            const payload = data.payload;
            if (
              payload !== undefined &&
              (typeof payload !== "object" ||
                payload === null ||
                Array.isArray(payload) ||
                Object.keys(payload).some(
                  (key) => key !== "title" && key !== "url",
                ) ||
                ("title" in payload &&
                  (typeof payload.title !== "string" ||
                    payload.title.length > 300)) ||
                ("url" in payload &&
                  (typeof payload.url !== "string" ||
                    payload.url.length > 2048)))
            )
              throw new Error("Invalid opened page");
            respond({
              ok: true,
              result: await browserAgentApi.admitTab(
                sessionId,
                payload === undefined
                  ? {}
                  : {
                      ...((payload as Record<string, unknown>).title !==
                      undefined
                        ? {
                            title: (payload as Record<string, string>).title,
                          }
                        : {}),
                      ...((payload as Record<string, unknown>).url !== undefined
                        ? { url: (payload as Record<string, string>).url }
                        : {}),
                    },
              ),
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
      title={null}
      overlayHeader
      onClose={onClose}
      bodyClassName="webskill-frame-body"
    >
      <div
        onPointerDownCapture={() => requestAgentControl()}
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
            onLoad={() => setFrameLoaded(true)}
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
