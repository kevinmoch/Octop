import type {
  ActionDownloadWatcher,
  DownloadedFileReader,
} from "@webskill/sdk/agent";
import {
  decodeBase64,
  type LocalBrowserHost,
  type LocalDownloadEntry,
} from "./localHost";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function asEntry(value: unknown): LocalDownloadEntry | undefined {
  const record = asRecord(value);
  if (
    !record ||
    typeof record.id !== "string" ||
    typeof record.name !== "string" ||
    typeof record.contentType !== "string" ||
    typeof record.size !== "number" ||
    typeof record.at !== "string"
  )
    return;
  return {
    id: record.id,
    name: record.name,
    contentType: record.contentType,
    size: record.size,
    at: record.at,
  };
}

async function listEntries(
  host: LocalBrowserHost,
): Promise<LocalDownloadEntry[]> {
  const raw = await host.downloads.list();
  if (!Array.isArray(raw)) throw new Error("The host download list is invalid");
  const entries = raw.map(asEntry);
  if (entries.some((entry) => entry === undefined)) {
    throw new Error("The host download list is invalid");
  }
  return entries as LocalDownloadEntry[];
}

/**
 * Watches the local browser's `will-download` window in the main process, so a
 * file saved by a local tab is visible to the WebSkill action policy.
 */
export function createLocalDownloadWatcher(
  host: LocalBrowserHost,
): ActionDownloadWatcher {
  return {
    async open() {
      const token = await host.downloads.openWindow();
      return {
        async settle({ timeoutMs }) {
          // The main process polls; one IPC call per settle instead of a
          // renderer-side loop that competes with page actions.
          return host.downloads.settleWindow(token, timeoutMs);
        },
      };
    },
  };
}

/** Local download reader: files saved by tabs, base64 over IPC. */
export function createLocalDownloadReader(
  host: LocalBrowserHost,
): DownloadedFileReader {
  return {
    async list() {
      return listEntries(host);
    },
    async read(id: string) {
      const result = await host.downloads.read(id);
      if (
        typeof result !== "object" ||
        result === null ||
        typeof result.contentType !== "string" ||
        typeof result.data !== "string"
      ) {
        throw new Error("The host download read response is invalid");
      }
      return {
        contentType: result.contentType,
        bytes: decodeBase64(result.data),
      };
    },
  };
}
