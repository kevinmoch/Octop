/**
 * R0 user-facing capability status.
 *
 * This is intentionally a short, reviewed projection of
 * docs/webskill-server-enhanced/capability-inventory.json. It must never claim
 * a port is available before the corresponding real evidence exists.
 */

export type CapabilityState =
  | "available"
  | "in-progress"
  | "planned"
  | "unsupported";

export interface CapabilityItem {
  id: string;
  zh: string;
  en: string;
  state: CapabilityState;
}

export interface CapabilityGroup {
  state: CapabilityState;
  zh: string;
  en: string;
  items: CapabilityItem[];
}

const available: CapabilityItem[] = [
  {
    id: "page-main-document",
    zh: "主文档感知与白名单动作",
    en: "Main document perception and allowlisted actions",
    state: "available",
  },
  {
    id: "page-frame-oopif",
    zh: "同源 frame、OOPIF 与嵌套 frame",
    en: "Same-origin frames, OOPIF and nested frames",
    state: "available",
  },
  {
    id: "target-workset",
    zh: "target 工作集与双指针",
    en: "Target workset and dual pointers",
    state: "available",
  },
  {
    id: "skill-governance",
    zh: "候选审核、发布与撤销",
    en: "Candidate review, publish and revoke",
    state: "available",
  },
  {
    id: "document-export-viewer",
    zh: "文档产物与隔离 viewer",
    en: "Document artifacts and isolated viewer",
    state: "available",
  },
  {
    id: "native-skill-generation",
    zh: "原生会话技能学习（R1 本地候选）",
    en: "Native session skill generation (R1 local candidate)",
    state: "available",
  },
];

const inProgress: CapabilityItem[] = [
  {
    id: "page-tabs",
    zh: "标签页 workset（R2A 实现中）",
    en: "Tab workset (R2A in progress)",
    state: "in-progress",
  },
  {
    id: "page-paging",
    zh: "大页面分段读取（R2A 实现中）",
    en: "Large-page perception paging (R2A in progress)",
    state: "in-progress",
  },
  {
    id: "capability-ui",
    zh: "能力状态与不支持替代路径",
    en: "Capability status and unsupported paths",
    state: "in-progress",
  },
];

const planned: CapabilityItem[] = [
  {
    id: "pdf-input",
    zh: "PDF 读取与逐页处理",
    en: "PDF reading and paging",
    state: "planned",
  },
  {
    id: "page-image",
    zh: "页面元素/区域图片",
    en: "Page element/region images",
    state: "planned",
  },
  {
    id: "downloads",
    zh: "当前会话下载文件",
    en: "Current-session downloads",
    state: "planned",
  },
];

const unsupported: CapabilityItem[] = [
  {
    id: "remote-image",
    zh: "外链图片（默认不支持）",
    en: "Remote images (unsupported by default)",
    state: "unsupported",
  },
  {
    id: "web-office",
    zh: "WebOffice 读取（默认不支持）",
    en: "WebOffice reading (unsupported by default)",
    state: "unsupported",
  },
  {
    id: "web-office-template",
    zh: "WebOffice 原件模板（默认不支持）",
    en: "WebOffice source templates (unsupported by default)",
    state: "unsupported",
  },
  {
    id: "dwg",
    zh: "DWG 读取与投放（默认不支持）",
    en: "DWG reading and delivery (unsupported by default)",
    state: "unsupported",
  },
  {
    id: "page-declared-skill",
    zh: "页面声明技能 / WebMCP（默认不支持）",
    en: "Page-declared skills / WebMCP (unsupported by default)",
    state: "unsupported",
  },
];

export const HOST_CAPABILITY_GROUPS: CapabilityGroup[] = [
  { state: "available", zh: "当前可用", en: "Available", items: available },
  {
    state: "in-progress",
    zh: "正在整改",
    en: "In progress",
    items: inProgress,
  },
  {
    state: "planned",
    zh: "后续阶段计划",
    en: "Planned in later phases",
    items: planned,
  },
  {
    state: "unsupported",
    zh: "明确不支持",
    en: "Explicitly unsupported",
    items: unsupported,
  },
];

export const HOST_CAPABILITY_ITEM_COUNT = HOST_CAPABILITY_GROUPS.reduce(
  (count, group) => count + group.items.length,
  0,
);
