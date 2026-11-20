import { lazy, Suspense, useEffect, useState } from "react";
import { Chatbot } from "@webskill/chatbot";
import type {
  RuntimeAppearanceConfig,
  SettingsSectionId,
} from "@webskill/chatbot";
import { useHostAppearance } from "./appearance";
import type { HostInit } from "./bridge";
import type { ConsolePage, WebSkillHost } from "./host";
import { createSandboxExecutor } from "./scriptExecutor";

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
  const locale = appearance.locale;

  useEffect(() => {
    const notify = () => {
      window.parent.postMessage(
        { channel: "octop-webskill-p1", type: "interact" },
        window.location.origin,
      );
    };
    document.addEventListener("pointerdown", notify, true);
    document.addEventListener("focusin", notify, true);
    return () => {
      document.removeEventListener("pointerdown", notify, true);
      document.removeEventListener("focusin", notify, true);
    };
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.classList.toggle(
      "dark",
      appearance.theme === "dark",
    );
    document.documentElement.style.colorScheme = appearance.theme;
  }, [appearance.theme, locale]);

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
            {copy(locale, "AI 助手", "AI Assistant")}
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
      </header>
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
    </div>
  );
}
