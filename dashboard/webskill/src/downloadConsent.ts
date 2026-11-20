import type {
  DownloadedFileAction,
  DownloadedFileConsent,
  DownloadedFileEntry,
} from "@webskill/sdk/agent";

const KEY_PREFIX = "octop.webskill.download-consent.1";
const LABELS: Record<DownloadedFileAction, { zh: string; en: string }> = {
  list: { zh: "列出下载目录", en: "list your downloads" },
  read: { zh: "读取下载的文件", en: "read files from your downloads" },
};

/**
 * Remembering covers the browser download directory, not one file. This is
 * the same granularity the extension host uses; do not key by file name.
 */
export function createDownloadConsent(
  locale: () => "zh" | "en",
): DownloadedFileConsent {
  const keyOf = (action: DownloadedFileAction) => `${KEY_PREFIX}.${action}`;
  return {
    async recall(action: DownloadedFileAction) {
      return localStorage.getItem(keyOf(action)) !== null;
    },
    async remember(action: DownloadedFileAction, _entry?: DownloadedFileEntry) {
      localStorage.setItem(
        keyOf(action),
        JSON.stringify({
          scope: "downloads",
          action,
          grantedAt: new Date().toISOString(),
        }),
      );
    },
    describeScope(action: DownloadedFileAction) {
      return locale() === "zh"
        ? `以后不再询问「${LABELS[action].zh}」`
        : `Don't ask again to ${LABELS[action].en}`;
    },
  };
}
