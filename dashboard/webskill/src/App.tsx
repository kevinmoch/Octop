import { lazy, Suspense, useEffect, useState } from "react";
import { Chatbot } from "@webskill/chatbot";
import type {
  RuntimeAppearanceConfig,
  SettingsSectionId,
} from "@webskill/chatbot";
import { useHostAppearance } from "./appearance";
import type { HostInit } from "./bridge";
import type { ConsolePage, WebSkillHost } from "./host";
import { isLoopbackNoAuthEndpoint, type RuntimeLlmEntry } from "./model";
import { createSandboxExecutor } from "./scriptExecutor";
import { HOST_CAPABILITY_GROUPS } from "./capabilityStatus";

const Console = lazy(async () => {
  await import("./monaco").then(({ configureMonaco }) => configureMonaco());
  return import("@webskill/console").then((module) => ({
    default: module.Console,
  }));
});

function copy(locale: string, zh: string, en: string): string {
  return locale === "zh" ? zh : en;
}

export default function App({
  host,
  init,
}: {
  host: WebSkillHost;
  init: HostInit;
}) {
  const fallback: RuntimeAppearanceConfig = {
    theme: init.theme,
    locale: init.locale,
    renderer: "native",
    dictationLang: "",
  };
  const appearance = useHostAppearance(host.runtimeConfig, fallback);
  const [view, setView] = useState<"chat" | "console">("chat");
  const [consolePage, setConsolePage] = useState<ConsolePage | undefined>();
  const [focusRunId, setFocusRunId] = useState<string | undefined>();
  const [focusCandidateId, setFocusCandidateId] = useState<
    string | undefined
  >();
  const [recordingBusy, setRecordingBusy] = useState(false);
  const [recordingError, setRecordingError] = useState("");
  const [recordingStatus, setRecordingStatus] = useState("");
  const [activeModel, setActiveModel] = useState<RuntimeLlmEntry | undefined>();
  const [, setNoAuthRevision] = useState(0);
  const [settingsError, setSettingsError] = useState("");
  const locale = appearance.locale;

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.classList.toggle(
      "dark",
      appearance.theme === "dark",
    );
    document.documentElement.style.colorScheme = appearance.theme;
  }, [appearance.theme, locale]);

  useEffect(() => {
    let live = true;
    const update = () => {
      void host.runtimeConfig.load().then((config) => {
        if (!live) return;
        setActiveModel(
          config.llm.entries.find(
            (entry) => entry.id === config.llm.defaultId,
          ) ?? config.llm.entries[0],
        );
      });
    };
    update();
    const unsubscribe = host.runtimeConfig.subscribe?.(update);
    return () => {
      live = false;
      unsubscribe?.();
    };
  }, [host.runtimeConfig]);

  const openConsole = (page?: SettingsSectionId) => {
    setConsolePage(page);
    setFocusRunId(undefined);
    setFocusCandidateId(undefined);
    setView("console");
  };
  host.navigation.openConsole = openConsole;
  host.navigation.openTrace = (runId) => {
    setFocusCandidateId(undefined);
    setFocusRunId(runId);
    setView("console");
  };
  host.navigation.openConsoleCandidate = (candidateId) => {
    setFocusRunId(undefined);
    setFocusCandidateId(candidateId);
    setConsolePage("governance.review");
    setView("console");
  };
  const createRecordedCandidate = async () => {
    setRecordingBusy(true);
    setRecordingError("");
    setRecordingStatus("");
    try {
      const result = await host.createRecordedCandidate();
      if (result.status === "submitted")
        host.navigation.openConsoleCandidate(result.candidateId);
    } catch {
      setRecordingError(
        copy(
          locale,
          "无法转换录制。请先完成并停止一段浏览器录制，然后重试。",
          "Could not convert the recording. Complete and stop a browser recording, then retry.",
        ),
      );
    } finally {
      setRecordingBusy(false);
    }
  };
  const replayRecording = async () => {
    setRecordingBusy(true);
    setRecordingError("");
    setRecordingStatus("");
    try {
      const result = await host.replayRecorded();
      if (result.status === "passed")
        setRecordingStatus(
          copy(locale, "录制回放已完成。", "Recording replay completed."),
        );
      else if (result.status === "failed")
        setRecordingError(
          copy(
            locale,
            "回放已停止。请检查页面变化或重新录制；不会自动重放后续步骤。",
            "Replay stopped. Review the page changes or record again; subsequent steps are not replayed automatically.",
          ),
        );
    } catch {
      setRecordingError(
        copy(
          locale,
          "无法回放。请先完成并停止一段录制，并确认当前浏览器会话有效。",
          "Could not replay. Complete and stop a recording, and check that the browser session is still active.",
        ),
      );
    } finally {
      setRecordingBusy(false);
    }
  };
  const deleteRecording = async () => {
    setRecordingBusy(true);
    setRecordingError("");
    setRecordingStatus("");
    try {
      const result = await host.deleteRecorded();
      if (result.status === "deleted")
        setRecordingStatus(
          copy(
            locale,
            "录制及其原始数据已删除；已发布技能未改变。",
            "The recording and its source data were deleted; published skills were not changed.",
          ),
        );
    } catch {
      setRecordingError(
        copy(
          locale,
          "无法删除录制。请先停止录制，并确认仍存在可删除的录制。",
          "Could not delete the recording. Stop it first and confirm that a deletable recording exists.",
        ),
      );
    } finally {
      setRecordingBusy(false);
    }
  };
  const localNoAuth = activeModel ? host.noAuth.has(activeModel.id) : false;

  return (
    <div
      className={`host-shell${appearance.theme === "dark" ? " dark" : ""}`}
      data-theme={appearance.theme}
    >
      <header className="host-header">
        <nav aria-label={copy(locale, "WebSkill 视图", "WebSkill views")}>
          <button
            type="button"
            className="host-tab"
            aria-pressed={view === "chat"}
            onClick={() => setView("chat")}
          >
            {copy(locale, "助手", "Assistant")}
          </button>
          <button
            type="button"
            className="host-tab"
            aria-pressed={view === "console"}
            onClick={() => openConsole()}
          >
            {copy(locale, "控制台", "Console")}
          </button>
        </nav>
        <button
          type="button"
          className="host-tab"
          data-testid="recording-skill-create"
          disabled={recordingBusy}
          onClick={() => void createRecordedCandidate()}
        >
          {copy(locale, "录制转技能", "Recording to skill")}
        </button>
        <button
          type="button"
          className="host-tab"
          data-testid="recording-replay"
          disabled={recordingBusy}
          onClick={() => void replayRecording()}
        >
          {copy(locale, "回放录制", "Replay recording")}
        </button>
        <button
          type="button"
          className="host-tab"
          data-testid="recording-delete"
          disabled={recordingBusy}
          onClick={() => void deleteRecording()}
        >
          {copy(locale, "删除录制", "Delete recording")}
        </button>
        <span className="host-stage">P3 candidate</span>
      </header>
      {recordingError && (
        <p role="alert" className="host-error">
          {recordingError}
        </p>
      )}
      {recordingStatus && (
        <p role="status" className="host-status">
          {recordingStatus}
        </p>
      )}
      {view === "chat" && activeModel && (
        <details className="local-model">
          <summary>
            {copy(locale, "本地模型访问", "Local model access")}
          </summary>
          <p>{activeModel.label}</p>
          {isLoopbackNoAuthEndpoint(activeModel) ? (
            <label>
              <input
                type="checkbox"
                checked={localNoAuth}
                onChange={(event) => {
                  if (!activeModel) return;
                  try {
                    host.noAuth.set(activeModel.id, event.target.checked);
                    setNoAuthRevision((value) => value + 1);
                    setSettingsError("");
                  } catch {
                    setSettingsError(
                      copy(
                        locale,
                        "无法保存本地模型偏好，请检查浏览器存储权限。",
                        "Could not save the local model preference. Check browser storage access.",
                      ),
                    );
                  }
                }}
              />
              {copy(
                locale,
                "此本地服务无需 API 密钥（仅回环地址）",
                "This local service does not require an API key (loopback only)",
              )}
            </label>
          ) : (
            <p>
              {copy(
                locale,
                "鉴权模型请在控制台中配置密钥。",
                "Configure credentials for authenticated providers in the Console.",
              )}
            </p>
          )}
          {settingsError && (
            <p role="alert" className="host-error">
              {settingsError}
            </p>
          )}
        </details>
      )}
      <main className="host-body">
        {view === "chat" ? (
          <Chatbot
            adapter={host.adapter}
            config={{
              chatRoot: "/chat",
              runtimeConfig: host.runtimeConfig,
              llm: host.llm,
              skillCandidates: host.skillCandidates,
              executorFactory: createSandboxExecutor,
            }}
            layout="embedded"
            sessionList="collapsed"
            docked
            onOpenSettings={openConsole}
            onEngineReady={host.connectEngine}
          />
        ) : (
          <Suspense
            fallback={
              <div role="status" className="console-loading">
                {copy(locale, "正在加载控制台", "Loading Console")}
              </div>
            }
          >
            <Console
              key={focusRunId ?? focusCandidateId ?? consolePage ?? "console"}
              backend={host.backend}
              governance={host.governance}
              connect={host.connect}
              runtimeConfig={host.runtimeConfig}
              hostCapabilities={{ downloadedFiles: false }}
              initialPage={
                focusRunId !== undefined
                  ? "runs.inspector"
                  : focusCandidateId !== undefined
                  ? "governance.review"
                  : consolePage
              }
              onNavigate={setConsolePage}
              {...(focusRunId !== undefined ? { focusRunId } : {})}
              {...(focusCandidateId !== undefined ? { focusCandidateId } : {})}
            />
          </Suspense>
        )}
      </main>
      <footer className="host-footer">
        <p>
          {copy(
            locale,
            "P3 本地候选：已具备的请见能力状态；当前不是生产支持版本。",
            "P3 local candidate: see capability status for what is actually available; this is not production support.",
          )}
        </p>
        <details className="capability-status" data-testid="capability-status">
          <summary>
            {copy(locale, "能力状态（R0）", "Capability status (R0)")}
          </summary>
          {HOST_CAPABILITY_GROUPS.map((group) => (
            <section key={group.state} className="capability-group">
              <h2>{copy(locale, group.zh, group.en)}</h2>
              <ul>
                {group.items.map((item) => (
                  <li key={item.id}>
                    <span className={`capability-badge ${group.state}`}>
                      {copy(locale, group.zh, group.en)}
                    </span>
                    {copy(locale, item.zh, item.en)}
                  </li>
                ))}
              </ul>
            </section>
          ))}
          <p>
            {copy(
              locale,
              "原生技能学习已在 R1 本地候选用真实外部模型闭环；录制/回放仍是 Octop 辅助能力，不等于完整 WebSkill。",
              "Native skill generation passed the R1 local candidate with a real external model; recording/replay remains an Octop assistant capability, not full WebSkill parity.",
            )}
          </p>
        </details>
      </footer>
    </div>
  );
}
