import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Tooltip } from "antd";
import {
  ArrowLeft,
  ArrowRight,
  Globe,
  Monitor,
  Plus,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import WebSkillFrame from "../../../components/WebSkillFrame";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useBrowserTabs } from "../../../hooks/useBrowserTabs";
import { useDefaultBrowserUrl } from "../../../hooks/useDefaultBrowserUrl";
import { useWebSkillAgentSession } from "../WebSkillBrowser/useWebSkillAgentSession";
import styles from "./index.module.less";

/**
 * Desktop Control → Browser, local mode.
 *
 * Layout mirrors the remote browser: the tab strip and address bar live inside
 * the browser column, and the AI panel is a sibling that owns its own divider.
 * That keeps the native WebContentsView bounds in sync when the divider moves.
 */
export default function LocalBrowserPage({
  onUseRemote,
}: {
  /** Desktop-only fallback: switch Control → Browser back to the stream. */
  onUseRemote?: () => void;
}) {
  const { t } = useTranslation();
  const currentUser = useCurrentUser();
  const [error, setError] = useState<string | null>(null);
  // The browser view covers the viewport, so failures must render inside the
  // page instead of an antd toast that would sit behind the native view.
  const showError = useCallback((text: string) => setError(text), []);
  const defaultBrowserUrl = useDefaultBrowserUrl();
  const {
    host,
    tabs,
    activeTab,
    createTab,
    closeTab,
    activateTab,
    navigate,
    goBack,
    goForward,
    reload,
    setViewport,
    setVisible,
  } = useBrowserTabs({ onError: showError, defaultUrl: defaultBrowserUrl });
  const {
    sessionId: agentSessionId,
    issueTicket: issueAgentTicket,
    renewSession: renewAgentSession,
  } = useWebSkillAgentSession(true, currentUser?.id);
  const [navUrl, setNavUrl] = useState("");
  const [isAiPanelOpen, setIsAiPanelOpen] = useState(true);
  const [boundsReady, setBoundsReady] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);

  // Local page-agent traffic goes over IPC, so nothing issues a PageAgent
  // ticket here; the WebSkill iframe still needs a backend session id for its
  // host handshake (and for the model/console APIs it shares with server mode).
  useEffect(() => {
    void issueAgentTicket().catch(() => undefined);
  }, [issueAgentTicket]);

  // Views are stacked above the window: hide them while this page is not shown,
  // and only reveal them after the placeholder has real bounds (otherwise a
  // remount can paint the previous frame at a stale position for one frame).
  useEffect(() => {
    if (!host || !boundsReady) return;
    setVisible(true);
    return () => setVisible(false);
  }, [host, boundsReady, setVisible]);

  useEffect(() => {
    setNavUrl(activeTab?.url ?? "");
  }, [activeTab?.id, activeTab?.url]);

  const activeTabId = activeTab?.id;

  // The panel divider changes the viewport's width, not the window's: observe
  // the element itself so the native view follows the drag.
  useEffect(() => {
    const element = viewportRef.current;
    if (!host || !activeTabId || !element) return;
    const updateBounds = () => {
      const rect = element.getBoundingClientRect();
      setViewport(activeTabId, {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      });
      if (rect.width > 0 && rect.height > 0) setBoundsReady(true);
    };
    updateBounds();
    const observer = new ResizeObserver(updateBounds);
    observer.observe(element);
    window.addEventListener("resize", updateBounds);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", updateBounds);
    };
  }, [host, activeTabId, isAiPanelOpen, setViewport]);

  const submitAddress = useCallback(() => {
    if (!activeTab) return;
    const target = navUrl.trim();
    if (!target) return;
    void navigate(activeTab.id, target);
  }, [activeTab, navUrl, navigate]);

  if (!host) {
    return <div className={styles.empty}>{t("localBrowser.unavailable")}</div>;
  }

  return (
    <div className={styles.pageBody}>
      {error && (
        <Alert
          type="error"
          showIcon
          closable
          message={error}
          onClose={() => setError(null)}
        />
      )}
      <div className={styles.mainRow}>
        <div className={styles.browserColumn}>
          <div className={styles.tabBar}>
            {tabs.map((tab) => (
              <div
                key={tab.id}
                className={`${styles.tab} ${
                  tab.active ? styles.tabActive : ""
                }`}
                onClick={() => void activateTab(tab.id)}
              >
                <Globe size={12} />
                <span className={styles.tabTitle}>{tab.title || tab.url}</span>
                <button
                  type="button"
                  className={styles.tabClose}
                  aria-label={t("localBrowser.closeTab")}
                  onClick={(event) => {
                    event.stopPropagation();
                    void closeTab(tab.id);
                  }}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            <button
              type="button"
              className={styles.newTab}
              aria-label={t("localBrowser.newTab")}
              onClick={() => void createTab()}
            >
              <Plus size={14} />
            </button>
          </div>
          <div className={styles.addressBar}>
            <Tooltip title={t("localBrowser.back")}>
              <Button
                size="small"
                icon={<ArrowLeft size={14} />}
                disabled={!activeTab?.canGoBack}
                onClick={() => activeTab && void goBack(activeTab.id)}
              />
            </Tooltip>
            <Tooltip title={t("localBrowser.forward")}>
              <Button
                size="small"
                icon={<ArrowRight size={14} />}
                disabled={!activeTab?.canGoForward}
                onClick={() => activeTab && void goForward(activeTab.id)}
              />
            </Tooltip>
            <Tooltip title={t("localBrowser.reload")}>
              <Button
                size="small"
                icon={<RotateCcw size={14} />}
                disabled={!activeTab}
                onClick={() => activeTab && void reload(activeTab.id)}
              />
            </Tooltip>
            <Input
              size="small"
              className={styles.urlInput}
              value={navUrl}
              placeholder={t("localBrowser.addressPlaceholder")}
              onChange={(event) => setNavUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitAddress();
              }}
              prefix={<Globe size={13} />}
            />
            <Button
              size="small"
              type="primary"
              disabled={!activeTab}
              onClick={submitAddress}
            >
              {t("localBrowser.go")}
            </Button>
            <Tooltip title={t("localBrowser.toggleAi")}>
              <Button
                size="small"
                type={isAiPanelOpen ? "primary" : "default"}
                icon={<Sparkles size={14} />}
                aria-pressed={isAiPanelOpen}
                onClick={() => setIsAiPanelOpen((open) => !open)}
              />
            </Tooltip>
            {onUseRemote && (
              <Tooltip title={t("localBrowser.useRemoteHint")}>
                <Button
                  size="small"
                  icon={<Monitor size={14} />}
                  onClick={() => {
                    // Hide the native tabs before the remote page mounts so the
                    // old WebContentsView never paints over it.
                    setVisible(false);
                    onUseRemote();
                  }}
                >
                  {t("localBrowser.useRemote")}
                </Button>
              </Tooltip>
            )}
          </div>
          <div ref={viewportRef} className={styles.viewport} />
        </div>
        {isAiPanelOpen && (
          <WebSkillFrame
            sessionId={agentSessionId}
            userId={currentUser?.id ?? 0}
            browserProfile={null}
            browserActive={Boolean(activeTab)}
            controlOwner="agent"
            hidden={false}
            placement="right"
            placementLocked
            onPlacementChange={() => undefined}
            onSessionExpired={renewAgentSession}
            onClose={() => setIsAiPanelOpen(false)}
            localTabId={activeTab?.id}
          />
        )}
      </div>
    </div>
  );
}
