import { createLocalStorageRuntimeConfigStore } from "@webskill/chatbot";
import type {
  ChatEngine,
  ChatbotHostAdapter,
  SettingsSectionId,
} from "@webskill/chatbot";
import { createFsConsoleBackend, createFsTraceSource } from "@webskill/console";
import type { ConsoleProps } from "@webskill/console";
import {
  FsAuditLog,
  PageActionPolicy,
  PagePerceptionPolicy,
  createBrowserChatbotHost,
  createRemotePageActionExecutor,
  createRemotePerceptionReader,
  createRemoteTargetRegistry,
  explainResolution,
} from "./sdkExports";
import type { PageActionUi } from "@webskill/sdk/agent";
import { OpfsProvider } from "@webskill/sdk/browser";
import type { HostInit } from "./bridge";
import { OctopHostBridge } from "./bridge";
import { LocalNoAuthRegistry, createHostLlmClient } from "./model";
import { OctopPageAgentTransport } from "./transport";

export type ConsolePage = NonNullable<ConsoleProps["initialPage"]>;

export async function createWebSkillHost(
  init: HostInit,
  bridge: OctopHostBridge,
) {
  const navigation: {
    openConsole: (page?: SettingsSectionId) => void;
    openTrace: (runId: string) => void;
  } = {
    openConsole: () => {},
    openTrace: () => {},
  };
  const fs = new OpfsProvider({
    rootName: `octop-webskill-user-${init.userId}`,
  });
  for (const root of ["/skills", "/managed", "/chat"]) {
    if (!(await fs.exists(root))) await fs.mkdir(root);
  }
  const runtimeConfig = createLocalStorageRuntimeConfigStore({
    key: `octop.webskill.runtime.${init.userId}`,
  });
  const initialConfig = await runtimeConfig.load();
  if (
    initialConfig.appearance.theme !== init.theme ||
    initialConfig.appearance.locale !== init.locale
  ) {
    await runtimeConfig.save({
      ...initialConfig,
      appearance: {
        ...initialConfig.appearance,
        theme: init.theme,
        locale: init.locale,
      },
    });
  }
  const noAuth = new LocalNoAuthRegistry(init.userId);
  const audit = new FsAuditLog({ root: "/managed", fs });
  const targets = createRemoteTargetRegistry();
  const transport = new OctopPageAgentTransport(bridge);
  let currentPage: { key: string; url?: string; title?: string } | undefined;
  const pagePerception = new PagePerceptionPolicy({
    scope: { include: ["body"] },
    reader: createRemotePerceptionReader({
      transport,
      targets,
      onDocument: (document) => {
        currentPage = document;
      },
    }),
    describePage: () => currentPage,
    audit,
  });
  const pageActionUi: { current?: PageActionUi } = {};
  const pageActions = new PageActionPolicy({
    scope: { include: ["body"] },
    executor: createRemotePageActionExecutor({ transport, targets }),
    ui: {
      request: (input) =>
        pageActionUi.current?.request(input) ??
        Promise.resolve({ id: input.id, cancelled: true }),
    },
    audit,
    diagnose: (ref) => explainResolution(targets.resolve(ref)),
    describePage: () => currentPage,
  });
  const bundle = createBrowserChatbotHost({
    fs,
    managedRoot: "/managed",
    skillRoots: ["/skills", "/managed"],
    navigation: {
      openConsole: () => navigation.openConsole(),
      openConsoleTrace: navigation.openTrace,
    },
    pagePerception,
  });
  const adapter: ChatbotHostAdapter = {
    ...bundle.chatbotAdapter,
    pagePerception,
    pageActions,
  };
  const backend = createFsConsoleBackend({
    fs,
    roots: ["/skills", "/managed"],
    manager: bundle.chatbotAdapter.skillManager,
    traces: createFsTraceSource(fs, "/chat"),
    versionStoreRoot: "/managed",
    chatRoot: "/chat",
  });
  return {
    fs,
    runtimeConfig,
    noAuth,
    llm: createHostLlmClient(runtimeConfig, noAuth),
    adapter,
    pageActionUi,
    backend,
    navigation,
    connectEngine: (engine: ChatEngine) => {
      pageActionUi.current = engine.bridge;
    },
    dispose: () => transport.dispose(),
  };
}

export type WebSkillHost = Awaited<ReturnType<typeof createWebSkillHost>>;
export type { ConsoleProps };
