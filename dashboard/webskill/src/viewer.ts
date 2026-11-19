import {
  startViewerShell,
  watchBlockedResources,
  type ViewerShellHost,
} from "@webskill/sdk/browser";
import {
  mountViewerComponents,
  type ViewerComponentsHandle,
} from "@webskill/sdk/ui";
import { setupViewerExport } from "./documentViewerExport/index";
import type { SlideDeckHandle, SlideView } from "./documentViewerSlides";
import { createViewerToast } from "./documentViewerToast";

function mustFind(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (!element) throw new Error(`The viewer shell is missing #${id}`);
  return element;
}

const content = mustFind("viewer-content");
const style = mustFind("viewer-skill-style");
const blocked = mustFind("viewer-blocked");
const blockedSession = watchBlockedResources(window, blocked);
const zh = new URLSearchParams(location.search).get("lang") !== "en";
document.documentElement.lang = zh ? "zh-CN" : "en";
mustFind("viewer-print-label").textContent = zh ? "打印" : "Print";
const exportUi = {
  button: mustFind("viewer-export") as HTMLButtonElement,
  label: mustFind("viewer-export-label"),
  notice: createViewerToast({
    root: mustFind("viewer-toast"),
    body: mustFind("viewer-export-note"),
    close: mustFind("viewer-toast-close") as HTMLButtonElement,
  }),
  live: content,
};
for (const [id, label] of [
  ["viewer-chrome-hide", zh ? "隐藏工具条" : "Hide the toolbar"],
  ["viewer-chrome-restore", zh ? "显示打印按钮" : "Show the print button"],
  ["viewer-toast-close", zh ? "关闭提示" : "Dismiss the notice"],
] as const) {
  mustFind(id).setAttribute("aria-label", label);
  mustFind(id).title = label;
}

const listeners = new Map<
  (event: MessageEvent) => void,
  (event: MessageEvent) => void
>();
const host: ViewerShellHost = {
  opener: window.parent,
  addEventListener: (_type, listener) => {
    const wrapped = (event: MessageEvent) => {
      if (event.source === window.parent) listener(event);
    };
    listeners.set(listener, wrapped);
    window.addEventListener("message", wrapped);
  },
  removeEventListener: (_type, listener) => {
    const wrapped = listeners.get(listener);
    if (wrapped) window.removeEventListener("message", wrapped);
    listeners.delete(listener);
  },
};

const sessionValues = new Map<string, string>();
Object.defineProperty(window, "sessionStorage", {
  value: {
    get length() {
      return sessionValues.size;
    },
    clear: () => sessionValues.clear(),
    getItem: (key: string) => sessionValues.get(key) ?? null,
    key: (index: number) => [...sessionValues.keys()][index] ?? null,
    removeItem: (key: string) => sessionValues.delete(key),
    setItem: (key: string, value: string) =>
      sessionValues.set(key, String(value)),
  },
});

let components: ViewerComponentsHandle | undefined;
let deck: SlideDeckHandle | undefined;
let pristine = "";
const chromeBar = mustFind("viewer-chrome");
const chromeRestore = mustFind("viewer-chrome-restore");
const hideChrome = () => {
  chromeBar.hidden = true;
  chromeRestore.hidden = false;
};
const showChrome = () => {
  chromeBar.hidden = false;
  chromeRestore.hidden = true;
};
mustFind("viewer-chrome-hide").addEventListener("click", hideChrome);
chromeRestore.addEventListener("click", showChrome);

function docPref(key: string): string | undefined {
  return content.querySelector<HTMLElement>(
    "[data-viewer-chrome], [data-viewer-chart-font], [data-viewer-mode]",
  )?.dataset[key];
}

async function mount(view: SlideView, restore: boolean): Promise<void> {
  const print = mustFind("viewer-print") as HTMLButtonElement;
  print.disabled = true;
  components?.dispose();
  deck?.dispose();
  deck = undefined;
  if (restore) content.innerHTML = pristine;
  if (docPref("viewerChrome") === "hidden") hideChrome();
  else showChrome();
  const fontSizes =
    docPref("viewerChartFont") === "lg"
      ? { title: 16, axisLabel: 14, legend: 14 }
      : undefined;
  components = mountViewerComponents(
    content,
    fontSizes ? { fontSizes } : undefined,
  );
  if (docPref("viewerMode") === "slides") {
    const slides = await import("./documentViewerSlides");
    const root = content.querySelector<HTMLElement>(slides.SLIDE_ROOT_SELECTOR);
    if (root) deck = await slides.mountSlideDeck(root, view);
  }
  print.disabled = false;
}

async function printDocument(): Promise<void> {
  if (!deck) {
    window.print();
    return;
  }
  const { settleCharts } = await import("./documentViewerSlides");
  await mount("print", true);
  await settleCharts();
  window.print();
  await mount(null, true);
}
mustFind("viewer-print").addEventListener("click", () => void printDocument());

const session = startViewerShell(host, { content, style }, () => {
  pristine = content.innerHTML;
  setupViewerExport(pristine, exportUi, zh);
  const declared = docPref("viewerTitle");
  if (declared) document.title = declared;
  void mount(null, false);
});
window.addEventListener(
  "pagehide",
  () => {
    session.dispose();
    blockedSession.dispose();
    components?.dispose();
    deck?.dispose();
    for (const listener of listeners.values())
      window.removeEventListener("message", listener);
    listeners.clear();
  },
  { once: true },
);
