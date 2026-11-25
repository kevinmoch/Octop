import type { PageActionUi } from "@webskill/sdk/agent";
import {
  createDocumentSurfaceHost,
  type DocumentSurfaceAudit,
  type WindowLike,
} from "@webskill/sdk/browser";
import type { RuntimeConfigStore } from "@webskill/chatbot";

export function createHostDocumentSurface(input: {
  runtimeConfig: RuntimeConfigStore;
  pageActionUi: { current?: PageActionUi };
  audit: DocumentSurfaceAudit;
}) {
  const open = (): WindowLike | null => {
    const host = document.querySelector<HTMLElement>(".host-shell");
    if (!host) return null;
    const shell = document.createElement("section");
    shell.className = "document-viewer-shell";
    const heading = document.createElement("header");
    heading.className = "document-viewer-header";
    const frame = document.createElement("iframe");
    frame.className = "document-viewer-frame";
    frame.dataset.testid = "document-viewer";
    frame.title = "WebSkill document viewer";
    frame.referrerPolicy = "no-referrer";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "host-tab";
    heading.append(close);
    shell.append(heading, frame);
    host.append(shell);
    const viewer = frame.contentWindow;
    if (!viewer) {
      shell.remove();
      return null;
    }
    const closeViewer = () => shell.remove();
    viewer.close = closeViewer;
    close.addEventListener("click", closeViewer);
    const locale = document.documentElement.lang === "zh" ? "zh" : "en";
    frame.src = `/webskill/viewer/?lang=${locale}`;
    const updateCopy = async () => {
      const config = await input.runtimeConfig.load();
      close.textContent =
        config.appearance.locale === "zh" ? "关闭文档" : "Close document";
      close.setAttribute(
        "aria-label",
        config.appearance.locale === "zh" ? "关闭文档" : "Close document",
      );
      shell.setAttribute(
        "aria-label",
        config.appearance.locale === "zh" ? "文档预览" : "Document preview",
      );
      shell.setAttribute("role", "region");
    };
    void updateCopy();
    return viewer;
  };

  return createDocumentSurfaceHost({
    viewerUrl: "/webskill/viewer/",
    open,
    audit: input.audit,
    confirm: async ({ skillName, dataSource, sizeNotice }) => {
      const config = await input.runtimeConfig.load();
      const locale = config.appearance.locale;
      const size =
        sizeNotice === undefined
          ? ""
          : locale === "zh"
          ? " 文档或图片较大，请确认内容来源可信。"
          : " The document or its images are large; confirm that the source is trusted.";
      const response = await input.pageActionUi.current?.request({
        type: "authorize",
        id: `document-surface-${crypto.randomUUID()}`,
        capability: "confirm",
        message:
          locale === "zh"
            ? `打开技能“${skillName}”生成的文档？来源：${dataSource}。文档在独立沙箱中显示，不能读取 CCS Buddy 或 WebSkill 的存储。${size}`
            : `Open the document produced by "${skillName}"? Source: ${dataSource}. It opens in an isolated sandbox and cannot read CCS Buddy or WebSkill storage.${size}`,
        details: { source: dataSource },
      });
      return (
        response !== undefined &&
        response.cancelled !== true &&
        response.value !== false
      );
    },
  });
}
