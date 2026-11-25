import type { RuntimeConfig } from "@webskill/chatbot";
import type {
  ConnectFacade,
  ConnectPageActionConsentView,
} from "@webskill/console";
import {
  createConnectFacade,
  createLocalStorageMcpVisibility,
  createPageMcpEndpoint,
} from "@webskill/sdk/mcp";
import type { RemoteEndpointStore } from "@webskill/sdk/mcp";
import type { PageActionConsentStore } from "./pageActionConsent";
import type { DownloadConsentStore } from "./downloadConsent";

function createHostRemoteEndpointStore(
  configKey: string,
  initialConfig: RuntimeConfig,
): RemoteEndpointStore {
  const readRaw = (): unknown => {
    try {
      const raw = globalThis.localStorage?.getItem(configKey);
      return raw ? JSON.parse(raw) : undefined;
    } catch {
      return undefined;
    }
  };
  return {
    load: () => {
      const stored = readRaw();
      if (
        typeof stored !== "object" ||
        stored === null ||
        Array.isArray(stored)
      ) {
        return initialConfig.mcp.endpoints;
      }
      const mcp = (stored as Record<string, unknown>)["mcp"];
      if (typeof mcp !== "object" || mcp === null || Array.isArray(mcp)) {
        return initialConfig.mcp.endpoints;
      }
      const endpoints = (mcp as Record<string, unknown>)["endpoints"];
      return Array.isArray(endpoints) ? endpoints : initialConfig.mcp.endpoints;
    },
    save: (endpoints) => {
      const stored = readRaw();
      const base =
        typeof stored === "object" && stored !== null && !Array.isArray(stored)
          ? (stored as Record<string, unknown>)
          : {};
      const currentMcp = base["mcp"];
      const mcp =
        typeof currentMcp === "object" &&
        currentMcp !== null &&
        !Array.isArray(currentMcp)
          ? (currentMcp as Record<string, unknown>)
          : {};
      globalThis.localStorage?.setItem(
        configKey,
        JSON.stringify({ ...base, mcp: { ...mcp, endpoints } }),
      );
    },
  };
}

const EMPTY_BUILTIN_WARNING =
  '[webskill] MCP endpoint "octop-webskill" is unavailable; its tools are omitted from this turn: MCP error -32601: Method not found';

/**
 * The integrated host has no page MCP endpoints. Custom remote endpoints come
 * from the same runtime configuration as the rest of the host, scoped per user.
 */
export function createHostConnectFacade(
  pageConsent: PageActionConsentStore,
  downloadConsent: DownloadConsentStore,
  options: {
    configKey: string;
    initialConfig: RuntimeConfig;
    userId: string | number;
  },
): ConnectFacade & {
  close(): Promise<void>;
  toolSource: ReturnType<typeof createPageMcpEndpoint>["toolSource"];
} {
  const host = createPageMcpEndpoint({
    endpoint: "octop-webskill",
    tools: [],
    webMcp: false,
    onWarning: (message) => {
      if (message !== EMPTY_BUILTIN_WARNING) console.warn(message);
    },
    visibility: createLocalStorageMcpVisibility(
      `octop.webskill.mcp.${options.userId}`,
    ),
  });
  const isDownload = (scope: string) => scope === "downloads";
  const facade = createConnectFacade({
    host,
    builtinEndpointUrl: "in-process://octop-webskill",
    remoteEndpoints: createHostRemoteEndpointStore(
      options.configKey,
      options.initialConfig,
    ),
    temporarySkills: async () => [],
    pageActionConsent: {
      list: async (): Promise<readonly ConnectPageActionConsentView[]> => [
        ...(await pageConsent.list()),
        ...(await downloadConsent.list()),
      ],
      forget: async (id: string) => {
        const scope = id.slice(0, id.lastIndexOf("|"));
        if (isDownload(scope)) return downloadConsent.forget(id);
        return pageConsent.forget(id);
      },
      forgetScope: async (scope: string) => {
        if (isDownload(scope)) return downloadConsent.forgetAll();
        return pageConsent.forgetScope(scope);
      },
    },
  });
  return Object.assign(facade, {
    close: async () => {
      for (const endpoint of host.registry.endpoints()) {
        if (endpoint === host.endpoint) continue;
        const client = host.registry.get(endpoint) as unknown as {
          close(): Promise<void>;
        };
        host.registry.unregister(endpoint);
        await client.close().catch(() => undefined);
      }
      await host.close();
    },
    toolSource: host.toolSource,
  });
}
