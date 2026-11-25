import { WEBSKILL_CONFIG } from "virtual:webskill-config";
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
import type {
  ActionDownloadWatcher,
  DownloadedFileReader,
  PageActionUi,
} from "@webskill/sdk/agent";
import {
  OpfsProvider,
  createDocxBlockReader,
  createPptxBlockReader,
  createXlsxBlockReader,
  createXlsxTableReader,
  extractDocxText,
  extractMsgText,
  extractPptxText,
  extractXlsxText,
  fillDocxTemplate,
  fillXlsxTemplate,
  readDocxTemplate,
  readXlsxTemplate,
} from "@webskill/sdk/browser";
import type { HostInit } from "./bridge";
import { HOST_CHANNEL, OctopHostBridge } from "./bridge";
import { createWebSkillRuntimeConfigStore } from "./configDefaults";
import { createHostDocumentSurface } from "./documentSurface";
import { LocalNoAuthRegistry, createHostLlmClient } from "./model";
import {
  deleteRecordedSteps,
  replayRecordedSteps,
  submitRecordedSkill,
} from "./recordingSkills";
import {
  OctopPageAgentTransport,
  PAGE_AGENT_PERCEPTION_TIMEOUT_MS,
} from "./transport";
import { OctopTabWorksetPort, createRemoteTabWatcher } from "./pageTabs";
import { createPageActionConsent } from "./pageActionConsent";
import { createDownloadConsent } from "./downloadConsent";
import { createHostConnectFacade } from "./connectFacade";
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
  const runtimeConfig = createWebSkillRuntimeConfigStore(
    init.userId,
    WEBSKILL_CONFIG,
  );
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
  const transport = new OctopPageAgentTransport(bridge, { include: ["body"] });
  let currentPage: { key: string; url?: string; title?: string } | undefined;
  const pagePerception = new PagePerceptionPolicy({
    scope: { include: ["body"] },
    reader: createRemotePerceptionReader({
      transport,
      targets,
      timeoutMs: PAGE_AGENT_PERCEPTION_TIMEOUT_MS,
      onDocument: (document) => {
        currentPage = document;
      },
    }),
    describePage: () => currentPage,
    audit,
  });
  const pageActionUi: { current?: PageActionUi } = {};
  const downloadConsent = createDownloadConsent({
    userId: init.userId,
    locale: () => init.locale,
  });
  const pageActionConsent = createPageActionConsent({
    userId: init.userId,
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
  const pageTabs = new TabWorksetPolicy({
    port: tabPort,
    enabled: () => pageActions?.enabled === true,
    audit,
    maxTabs: () => 8,
  });
  const notifiedDownloadFailures = new Set<string>();
  const notifyDownloadFailures = (result: unknown): void => {
    if (typeof result !== "object" || result === null) return;
    const raw = (result as Record<string, unknown>).failures;
    if (!Array.isArray(raw)) return;
    const fresh: Array<{ name: string; reason: string }> = [];
    for (const entry of raw) {
      if (typeof entry !== "object" || entry === null) continue;
      const record = entry as Record<string, unknown>;
      const name = typeof record.name === "string" ? record.name : "download";
      const reason =
        typeof record.reason === "string" ? record.reason : "unknown failure";
      const key =
        typeof record.guid === "string" ? record.guid : `${name}|${reason}`;
      if (notifiedDownloadFailures.has(key)) continue;
      notifiedDownloadFailures.add(key);
      fresh.push({ name, reason });
    }
    if (fresh.length === 0) return;
    window.parent.postMessage(
      { channel: HOST_CHANNEL, type: "download-failure", failures: fresh },
      window.location.origin,
    );
  };

  const downloadIds = async (): Promise<Set<string>> => {
    let result: unknown;
    try {
      result = await bridge.call("downloads-list");
    } catch {
      return new Set();
    }
    if (
      typeof result !== "object" ||
      result === null ||
      !Array.isArray((result as Record<string, unknown>).downloads)
    ) {
      return new Set();
    }
    notifyDownloadFailures(result);
    return new Set(
      (result as { downloads: Array<Record<string, unknown>> }).downloads
        .map((entry) => entry.id)
        .filter((id): id is string => typeof id === "string"),
    );
  };
  const downloadWatcher: ActionDownloadWatcher = {
    async open() {
      const before = await downloadIds();
      return {
        async settle({ timeoutMs }) {
          const end = Date.now() + timeoutMs;
          while (Date.now() < end) {
            const current = await downloadIds();
            const fresh = [...current].filter((id) => !before.has(id)).length;
            if (fresh > 0) return fresh;
            await new Promise((resolve) => setTimeout(resolve, 150));
          }
          return 0;
        },
      };
    },
  };

  const pageActions: PageActionPolicy = new PageActionPolicy({
    scope: { include: ["body"] },
    executor: createRemotePageActionExecutor({ transport, targets }),
    ui: {
      request: (input) =>
        pageActionUi.current?.request(input) ??
        Promise.resolve({ id: input.id, cancelled: true }),
    },
    consent: pageActionConsent,
    downloadWatcher,
    downloadSignalEnabled: () => true,
    downloadWaitMs: () => 1500,
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
      notifyDownloadFailures(result);
      const downloads = (
        result as { downloads: Array<Record<string, unknown>> }
      ).downloads;
      const failures = (result as { failures?: Array<Record<string, unknown>> })
        .failures;
      if (
        downloads.length === 0 &&
        Array.isArray(failures) &&
        failures.length > 0
      ) {
        const first = failures[0];
        const name = typeof first?.name === "string" ? first.name : "download";
        const reason =
          typeof first?.reason === "string" ? first.reason : "unknown failure";
        throw new Error(`The download could not be read: ${name}: ${reason}`);
      }
      return downloads
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
  const buildLinkedDocument: NonNullable<
    ChatbotHostAdapter["linkedDocuments"]
  > = {
    get origin() {
      if (currentPage?.url === undefined) return undefined;
      try {
        return new URL(currentPage.url).origin;
      } catch {
        return undefined;
      }
    },
    async read(url: string) {
      const result = await bridge.call("linked-document", { url });
      if (
        typeof result !== "object" ||
        result === null ||
        typeof (result as Record<string, unknown>).contentType !== "string" ||
        typeof (result as Record<string, unknown>).data !== "string"
      ) {
        throw new Error("The host linked-document response is invalid");
      }
      const binary = atob((result as Record<string, string>).data);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return {
        mimeType: (result as Record<string, string>).contentType,
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
    linkedDocuments: buildLinkedDocument,
    pdfExtractor,
    pdfDocumentReader,
    downloads: { reader: downloadReader, consent: downloadConsent },
    docxExtractor: extractDocxText,
    xlsxExtractor: extractXlsxText,
    pptxExtractor: extractPptxText,
    msgExtractor: extractMsgText,
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
    connect: createHostConnectFacade(pageActionConsent, downloadConsent),
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
