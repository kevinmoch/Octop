import type {
  PageActionConsent,
  PageActionKind,
  PageActionTarget,
} from "@webskill/sdk/agent";

const KEY_PREFIX = "octop.webskill.page-action-consent.1";
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
}): PageActionConsent {
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
  };
}
