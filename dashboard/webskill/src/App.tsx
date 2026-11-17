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
    setView("console");
  };
  host.navigation.openConsole = openConsole;
  host.navigation.openTrace = (runId) => {
    setFocusRunId(runId);
    setView("console");
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
        <span className="host-stage">P2 candidate</span>
      </header>
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
              key={focusRunId ?? consolePage ?? "console"}
              backend={host.backend}
              runtimeConfig={host.runtimeConfig}
              hostCapabilities={{ downloadedFiles: false }}
              initialPage={
                focusRunId !== undefined ? "runs.inspector" : consolePage
              }
              onNavigate={setConsolePage}
              {...(focusRunId !== undefined ? { focusRunId } : {})}
            />
          </Suspense>
        )}
      </main>
      <footer className="host-footer">
        {copy(
          locale,
          "P2 正在验证 frame 和多标签；技能录制回放与文档产物尚未开放，当前不是生产支持版本。",
          "P2 is validating frames and multiple targets. Skill recording, replay and document delivery are not open; this is not production support.",
        )}
      </footer>
    </div>
  );
}
