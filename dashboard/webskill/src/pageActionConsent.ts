import type {
  PageActionConsent,
  PageActionKind,
  PageActionTarget,
} from "@webskill/sdk/agent";

const KEY_PREFIX = "octop.webskill.page-action-consent.1";

interface StoredConsent {
  id: string;
  scope: string;
  action: string;
  grantedAt: string;
}

export interface PageActionConsentView {
  id: string;
  scope: string;
  action: string;
  grantedAt: string;
}

export interface PageActionConsentStore extends PageActionConsent {
  list(): Promise<readonly PageActionConsentView[]>;
  forget(id: string): Promise<void>;
  forgetScope(scope: string): Promise<void>;
}

function parseStored(raw: string | null): StoredConsent | undefined {
  if (raw === null) return undefined;
  try {
    const value: unknown = JSON.parse(raw);
    if (
      typeof value === "object" &&
      value !== null &&
      typeof (value as StoredConsent).id === "string" &&
      typeof (value as StoredConsent).scope === "string" &&
      typeof (value as StoredConsent).action === "string" &&
      typeof (value as StoredConsent).grantedAt === "string"
    ) {
      return value as StoredConsent;
    }
  } catch {
    // A corrupt record is treated as absent; asking again is fail-closed.
  }
  return undefined;
}

function idOf(origin: string, action: string): string {
  return `${origin}|${action}`;
}

function parseId(id: string): { origin: string; action: string } | undefined {
  const cut = id.lastIndexOf("|");
  if (cut <= 0 || cut === id.length - 1) return undefined;
  return { origin: id.slice(0, cut), action: id.slice(cut + 1) };
}
const ZH_ACTIONS: Record<PageActionKind, string> = {
  click: "点击",
  fill: "填写",
  submit: "提交",
  select: "选择",
  set: "开关",
  attach: "附件上传",
  scroll: "滚动",
  hover: "鼠标悬停",
  drag: "拖拽",
  back: "返回上一页",
};

/**
 * Octop host consent memory for page actions.
 *
 * The extension host already remembers by origin+action. Octop's integrated
 * host had no PageActionConsent at all, so the SDK correctly hid the checkbox.
 * Keep the same granularity; a target without a known origin is never stored.
 */
export function createPageActionConsent(options: {
  origin: () => string | undefined;
  locale: () => "zh" | "en";
}): PageActionConsentStore {
  const keyOf = (origin: string, action: string) =>
    `${KEY_PREFIX}.${encodeURIComponent(origin)}.${action}`;
  return {
    async recall(_target: PageActionTarget, action: PageActionKind) {
      const origin = options.origin();
      if (origin === undefined) return false;
      return localStorage.getItem(keyOf(origin, action)) !== null;
    },
    async remember(_target: PageActionTarget, action: PageActionKind) {
      const origin = options.origin();
      if (origin === undefined) return;
      localStorage.setItem(
        keyOf(origin, action),
        JSON.stringify({
          id: idOf(origin, action),
          scope: origin,
          action,
          grantedAt: new Date().toISOString(),
        }),
      );
    },
    describeScope(_target: PageActionTarget, action: PageActionKind) {
      const origin = options.origin();
      if (origin === undefined) return undefined;
      return options.locale() === "zh"
        ? `以后不再询问在 ${origin} 上的「${ZH_ACTIONS[action]}」操作`
        : `Don't ask again for “${action}” on ${origin}`;
    },
    async list() {
      const views: PageActionConsentView[] = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key === null || !key.startsWith(`${KEY_PREFIX}.`)) continue;
        const stored = parseStored(localStorage.getItem(key));
        if (stored !== undefined) views.push(stored);
      }
      return views;
    },
    async forget(id: string) {
      const parsed = parseId(id);
      if (parsed !== undefined)
        localStorage.removeItem(keyOf(parsed.origin, parsed.action));
    },
    async forgetScope(scope: string) {
      const doomed: string[] = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (
          key !== null &&
          key.startsWith(`${KEY_PREFIX}.${encodeURIComponent(scope)}.`)
        ) {
          doomed.push(key);
        }
      }
      for (const key of doomed) localStorage.removeItem(key);
    },
  };
}
