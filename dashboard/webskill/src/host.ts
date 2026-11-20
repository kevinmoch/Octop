import { createLocalStorageRuntimeConfigStore } from "@webskill/chatbot";
import type {
  ChatEngine,
  ChatbotHostAdapter,
  SettingsSectionId,
} from "@webskill/chatbot";
import {
  createFsConsoleBackend,
  createFsTraceSource,
  createGovernanceFacade,
} from "@webskill/console";
import type { ConsoleProps } from "@webskill/console";
import {
  CandidateStore,
  FsAuditLog,
  PERCEPTION_PAGING_DEFAULTS,
  PageActionPolicy,
  PagePerceptionPolicy,
  TabWorksetPolicy,
  createBrowserChatbotHost,
  createRemotePageActionExecutor,
  createRemotePageImageHost,
  createRemotePerceptionReader,
  createCandidateSink,
  createRemoteTargetRegistry,
  explainResolution,
} from "./sdkExports";
import type { DownloadedFileReader, PageActionUi } from "@webskill/sdk/agent";
import {
  OpfsProvider,
  createDocxBlockReader,
  createFetchLinkedDocumentReader,
  createPptxBlockReader,
  createXlsxBlockReader,
  createXlsxTableReader,
  extractDocxText,
  extractPptxText,
  extractXlsxText,
  fillDocxTemplate,
  fillXlsxTemplate,
  readDocxTemplate,
  readXlsxTemplate,
} from "@webskill/sdk/browser";
import type { HostInit } from "./bridge";
import { OctopHostBridge } from "./bridge";
import { createHostDocumentSurface } from "./documentSurface";
import { LocalNoAuthRegistry, createHostLlmClient } from "./model";
import {
  deleteRecordedSteps,
  replayRecordedSteps,
  submitRecordedSkill,
} from "./recordingSkills";
import { OctopPageAgentTransport } from "./transport";
import { OctopTabWorksetPort, createRemoteTabWatcher } from "./pageTabs";
import { createPageActionConsent } from "./pageActionConsent";
import { createDownloadConsent } from "./downloadConsent";
import { pdfDocumentReader, pdfExtractor } from "./pdf";

export type ConsolePage = NonNullable<ConsoleProps["initialPage"]>;

export async function createWebSkillHost(
  init: HostInit,
  bridge: OctopHostBridge,
) {
  const navigation: {
    openConsole: (page?: SettingsSectionId) => void;
    openTrace: (runId: string) => void;
    openConsoleCandidate: (candidateId: string) => void;
  } = {
    openConsole: () => {},
    openTrace: () => {},
    openConsoleCandidate: () => {},
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
  const skillCandidates = createCandidateSink({
    store: new CandidateStore({ root: "/managed", fs }),
    audit,
  });
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
  const downloadConsent = createDownloadConsent(() => init.locale);
  const pageActionConsent = createPageActionConsent({
    origin: () => {
      if (currentPage?.url === undefined) return undefined;
      try {
        return new URL(currentPage.url).origin;
      } catch {
        return undefined;
      }
    },
    locale: () => init.locale,
  });
  const tabPort = new OctopTabWorksetPort(bridge);
  let pageActions: PageActionPolicy | undefined;
  const pageTabs = new TabWorksetPolicy({
    port: tabPort,
    enabled: () => pageActions?.enabled === true,
    audit,
    maxTabs: () => 8,
  });
  pageActions = new PageActionPolicy({
    scope: { include: ["body"] },
    executor: createRemotePageActionExecutor({ transport, targets }),
    ui: {
      request: (input) =>
        pageActionUi.current?.request(input) ??
        Promise.resolve({ id: input.id, cancelled: true }),
    },
    consent: pageActionConsent,
    audit,
    diagnose: (ref) => explainResolution(targets.resolve(ref)),
    describePage: () => currentPage,
    tabWatcher: createRemoteTabWatcher(tabPort),
    workset: {
      admit: async (opened) => {
        const handle = await tabPort.admit(opened);
        return { admitted: true, handle };
      },
    },
  });
  const perceptionPaging = {
    enabled: true,
    maxNodes: PERCEPTION_PAGING_DEFAULTS.maxNodes,
    maxChars: PERCEPTION_PAGING_DEFAULTS.maxChars,
  };
  const bundle = createBrowserChatbotHost({
    fs,
    managedRoot: "/managed",
    skillRoots: ["/skills", "/managed"],
    navigation: {
      openConsole: () => navigation.openConsole(),
      openConsoleTrace: navigation.openTrace,
      openConsoleCandidate: navigation.openConsoleCandidate,
    },
    pagePerception,
  });

  const downloadReader: DownloadedFileReader = {
    async list() {
      const result = await bridge.call("downloads-list");
      if (
        typeof result !== "object" ||
        result === null ||
        !Array.isArray((result as Record<string, unknown>).downloads)
      ) {
        throw new Error("The host download list is invalid");
      }
      return (result as { downloads: Array<Record<string, unknown>> }).downloads
        .filter(
          (entry) =>
            typeof entry.id === "string" &&
            typeof entry.name === "string" &&
            typeof entry.contentType === "string" &&
            typeof entry.size === "number" &&
            typeof entry.at === "string",
        )
        .map((entry) => ({
          id: entry.id as string,
          name: entry.name as string,
          contentType: entry.contentType as string,
          size: entry.size as number,
          at: entry.at as string,
        }));
    },
    async read(id: string) {
      const result = await bridge.call("downloads-read", { id });
      if (
        typeof result !== "object" ||
        result === null ||
        typeof (result as Record<string, unknown>).contentType !== "string" ||
        typeof (result as Record<string, unknown>).data !== "string"
      ) {
        throw new Error("The host download read response is invalid");
      }
      const binary = atob((result as Record<string, string>).data);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return {
        contentType: (result as Record<string, string>).contentType,
        bytes,
      };
    },
  };
  const adapter: ChatbotHostAdapter = {
    ...bundle.chatbotAdapter,
    pagePerception,
    pagePerceptionPaging: perceptionPaging,
    pageActions,
    pageImage: createRemotePageImageHost({ transport }),
    pageTabs,
    linkedDocuments: createFetchLinkedDocumentReader(),
    pdfExtractor,
    pdfDocumentReader,
    downloads: { reader: downloadReader, consent: downloadConsent },
    docxExtractor: extractDocxText,
    xlsxExtractor: extractXlsxText,
    pptxExtractor: extractPptxText,
    docxDocumentReader: createDocxBlockReader(),
    xlsxDocumentReader: createXlsxBlockReader(),
    xlsxTableReader: createXlsxTableReader(),
    pptxDocumentReader: createPptxBlockReader(),
    docxTemplateEngine: {
      read: readDocxTemplate,
      fill: fillDocxTemplate,
    },
    xlsxTemplateEngine: {
      read: readXlsxTemplate,
      fill: fillXlsxTemplate,
    },
    documentAudit: audit,
    documentSurface: createHostDocumentSurface({
      runtimeConfig,
      pageActionUi,
      audit,
    }),
  };
  const backend = createFsConsoleBackend({
    fs,
    roots: ["/skills", "/managed"],
    manager: bundle.chatbotAdapter.skillManager,
    traces: createFsTraceSource(fs, "/chat"),
    versionStoreRoot: "/managed",
    chatRoot: "/chat",
  });
  const governance = createGovernanceFacade({
    fs,
    manager: bundle.chatbotAdapter.skillManager,
    governanceRoot: "/managed",
    chatRoot: "/chat",
  });
  return {
    fs,
    runtimeConfig,
    noAuth,
    llm: createHostLlmClient(runtimeConfig, noAuth),
    adapter,
    skillCandidates,
    pageActionUi,
    backend,
    governance,
    navigation,
    createRecordedCandidate: async (recordingId?: string) => {
      const config = await runtimeConfig.load();
      return submitRecordedSkill({
        bridge,
        ui: pageActionUi.current,
        sink: skillCandidates,
        locale: config.appearance.locale,
        ...(recordingId !== undefined ? { recordingId } : {}),
      });
    },
    replayRecorded: async (recordingId?: string) => {
      const config = await runtimeConfig.load();
      return replayRecordedSteps({
        bridge,
        ui: pageActionUi.current,
        locale: config.appearance.locale,
        ...(recordingId !== undefined ? { recordingId } : {}),
      });
    },
    deleteRecorded: async (recordingId?: string) => {
      const config = await runtimeConfig.load();
      return deleteRecordedSteps({
        bridge,
        ui: pageActionUi.current,
        locale: config.appearance.locale,
        ...(recordingId !== undefined ? { recordingId } : {}),
      });
    },
    connectEngine: (engine: ChatEngine) => {
      pageActionUi.current = engine.bridge;
    },
    dispose: () => transport.dispose(),
  };
}

export type WebSkillHost = Awaited<ReturnType<typeof createWebSkillHost>>;
export type { ConsoleProps };
