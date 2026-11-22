import type {
  DownloadedFileAction,
  DownloadedFileConsent,
  DownloadedFileEntry,
} from "@webskill/sdk/agent";

const KEY_PREFIX = "octop.webskill.download-consent.2";
export interface DownloadConsentView {
  id: string;
  scope: string;
  action: string;
  grantedAt: string;
}

export interface DownloadConsentStore extends DownloadedFileConsent {
  list(): Promise<readonly DownloadConsentView[]>;
  forget(id: string): Promise<void>;
  forgetAll(): Promise<void>;
}

const LABELS: Record<DownloadedFileAction, { zh: string; en: string }> = {
  list: { zh: "列出下载目录", en: "list your downloads" },
  read: { zh: "读取下载的文件", en: "read files from your downloads" },
};

/**
 * Remembering covers the browser download directory, not one file. This is
 * the same granularity the extension host uses; do not key by file name.
 */
export function createDownloadConsent(options: {
  userId: number;
  locale: () => "zh" | "en";
}): DownloadConsentStore {
  const userPrefix = `${KEY_PREFIX}.${options.userId}`;
  const keyOf = (action: DownloadedFileAction) => `${userPrefix}.${action}`;
  return {
    async recall(action: DownloadedFileAction) {
      return localStorage.getItem(keyOf(action)) !== null;
    },
    async remember(action: DownloadedFileAction, _entry?: DownloadedFileEntry) {
      localStorage.setItem(
        keyOf(action),
        JSON.stringify({
          id: `downloads|${action}`,
          scope: "downloads",
          action,
          grantedAt: new Date().toISOString(),
        }),
      );
    },
    describeScope(action: DownloadedFileAction) {
      return options.locale() === "zh"
        ? `以后不再询问「${LABELS[action].zh}」`
        : `Don't ask again to ${LABELS[action].en}`;
    },
    async list() {
      const views = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key === null || !key.startsWith(`${userPrefix}.`)) continue;
        const raw = localStorage.getItem(key);
        if (raw === null) continue;
        try {
          const value = JSON.parse(raw) as Record<string, unknown>;
          if (
            typeof value.id === "string" &&
            typeof value.scope === "string" &&
            typeof value.action === "string" &&
            typeof value.grantedAt === "string"
          ) {
            views.push(
              value as {
                id: string;
                scope: string;
                action: string;
                grantedAt: string;
              },
            );
          }
        } catch {
          // Corrupt entries are ignored; the next request asks again.
        }
      }
      return views;
    },
    async forget(id: string) {
      const action = id.slice(id.lastIndexOf("|") + 1);
      if (action === "list" || action === "read") {
        localStorage.removeItem(keyOf(action));
      }
    },
    async forgetAll() {
      const doomed: string[] = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key !== null && key.startsWith(`${userPrefix}.`)) doomed.push(key);
      }
      for (const key of doomed) localStorage.removeItem(key);
    },
  };
}
