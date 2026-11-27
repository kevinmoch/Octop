import type { ChatbotHostAdapter } from "@webskill/chatbot";
import { decodeBase64, type LocalBrowserHost } from "./localHost";

const MAX_LINKED_DOCUMENT_URL = 2048;
const MAX_LINKED_DOCUMENT_BYTES = 32 * 1024 * 1024;

/**
 * Reads a link the model found in the local page.
 *
 * The fetch runs inside the active tab (design doc §4.1.4), so it carries that
 * site's cookies and origin instead of being blocked by CORS on the Dashboard
 * origin.
 */
export function createLocalLinkedDocuments(
  host: LocalBrowserHost,
  resolveTabId: () => string | undefined,
  currentOrigin: () => string | undefined,
): NonNullable<ChatbotHostAdapter["linkedDocuments"]> {
  return {
    get origin() {
      return currentOrigin();
    },
    async read(url: string) {
      if (typeof url !== "string" || url.length > MAX_LINKED_DOCUMENT_URL) {
        throw new Error("The linked document URL is invalid");
      }
      let target: URL;
      try {
        target = new URL(url);
      } catch {
        throw new Error("The linked document URL is invalid");
      }
      if (target.protocol !== "http:" && target.protocol !== "https:") {
        throw new Error("Only http and https documents can be read");
      }
      const tabId = resolveTabId();
      if (!tabId) throw new Error("No browser tab is bound");
      const result = await host.frame.fetchInTab(tabId, target.toString(), {
        method: "GET",
      });
      if (result.status >= 400) {
        throw new Error(
          `The linked document could not be read (HTTP ${result.status})`,
        );
      }
      const bytes = decodeBase64(result.body);
      if (bytes.byteLength > MAX_LINKED_DOCUMENT_BYTES) {
        throw new Error("The linked document is too large to read");
      }
      return {
        mimeType:
          headerValue(result.headers, "content-type") ??
          "application/octet-stream",
        bytes,
      };
    },
  };
}

function headerValue(
  headers: Record<string, string>,
  name: string,
): string | undefined {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === wanted) return value;
  }
  return undefined;
}
