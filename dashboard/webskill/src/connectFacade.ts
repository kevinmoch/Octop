import type {
  ConnectEndpointView,
  ConnectFacade,
  ConnectPageActionConsentView,
  ConnectToolView,
  ConnectWebMcpSource,
} from "@webskill/console";
import type { PageActionConsentStore } from "./pageActionConsent";
import type { DownloadConsentStore } from "./downloadConsent";

/**
 * The integrated host has no page MCP endpoints. Console still needs a facade
 * to render the "remembered page actions" section from the same local stores
 * the side panel writes; empty endpoint/tool lists are the truthful answer.
 */
export function createHostConnectFacade(
  pageConsent: PageActionConsentStore,
  downloadConsent: DownloadConsentStore,
): ConnectFacade {
  const endpoint = (): never => {
    throw new Error("This host does not support remote MCP endpoints");
  };
  const webmcp: ConnectWebMcpSource = {
    label: "WebSkill page",
    isAvailable: () => false,
    isEnabled: () => false,
    setEnabled: () => undefined,
    listTools: async (): Promise<ConnectToolView[]> => [],
  };
  const isDownload = (scope: string) => scope === "downloads";
  return {
    listEndpoints: async (): Promise<ConnectEndpointView[]> => [],
    addEndpoint: async () => endpoint(),
    removeEndpoint: async () => endpoint(),
    reconnect: async () => endpoint(),
    listTools: async (): Promise<ConnectToolView[]> => [],
    testEndpoint: async () => ({ ok: true, latencyMs: 0 }),
    temporarySkills: async () => [],
    pageActionConsents: async (): Promise<
      readonly ConnectPageActionConsentView[]
    > => [...(await pageConsent.list()), ...(await downloadConsent.list())],
    forgetPageActionConsent: async (id: string) => {
      const scope = id.slice(0, id.lastIndexOf("|"));
      if (isDownload(scope)) return downloadConsent.forget(id);
      return pageConsent.forget(id);
    },
    forgetPageActionConsentScope: async (scope: string) => {
      if (isDownload(scope)) return downloadConsent.forgetAll();
      return pageConsent.forgetScope(scope);
    },
    webmcp,
  };
}
