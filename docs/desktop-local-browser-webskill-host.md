# Octop 桌面端本地浏览器与 WebSkill 宿主改造设计

## 1. 背景与目标

当前 Octop 桌面端「菜单 → 控制 → 浏览器」采用**后端浏览器 + WebSocket 推流**方案：
- 左侧 `BrowserViewer` 把后端 Playwright/Chromium 的 JPEG 帧画到 canvas；
- 用户输入通过 WebSocket 回传后端浏览器；
- 右侧 WebSkill 通过 iframe + `postMessage` 与宿主通信，页面感知/操作经 `/api/browser-agent/ws` 由后端执行。

本方案将桌面端改造为**本地真实浏览器标签页 + WebSkill 扩展型宿主**，达到与 `web-skill-sdk/examples/browser-extension` 一致的体验：

1. 左侧为真实浏览器标签页，可访问任意 `https://` 网站，导航无 CORS 错误；
2. 页面内 `window.open` / `target=_blank` 自动转为新标签页，仍在 Octop 内；
3. WebSkill 直接读取和操作左侧标签页 DOM，不再经后端浏览器中转；
4. `@webskill/sdk`、`@webskill/chatbot`、`@webskill/console` **零改动**，Octop 仅作为下游宿主；
5. 服务器模式保留现有推流方案，桌面端新增本地模式，互不影响。

参考实现：
- `web-skill-sdk/examples/browser-extension`：扩展宿主装配、`TabBinding`、`TabWorkset`、`PageAgentTransport`；
- `ccs-monorepo/apps/ccs-framework/electron/`：Electron 帧桥、preload 注入、下载观察、新窗口转达；
- `web-skill-site/src/demo/webskill/`：网页宿主 `ChatbotHostAdapter` 装配方式。

## 2. 现状关键代码

| 模块 | 文件 | 现状 |
|------|------|------|
| 浏览器页面 | `dashboard/src/pages/Control/RemoteBrowser/index.tsx` | canvas 推流 + iframe WebSkill |
| 浏览器视图 | `dashboard/src/components/BrowserViewer/index.tsx` | canvas 渲染、标签栏、地址栏 |
| WebSkill 面板 | `dashboard/src/components/WebSkillFrame.tsx` | iframe + `postMessage` 桥 |
| WebSkill 宿主 | `dashboard/webskill/src/host.ts` | 装配 `ChatbotHostAdapter`，走后端 WebSocket |
| WebSkill transport | `dashboard/webskill/src/transport.ts` | `OctopPageAgentTransport` → `/api/browser-agent/ws` |
| 标签页端口 | `dashboard/webskill/src/pageTabs.ts` | `OctopTabWorksetPort` → 后端 tabs API |
| Electron 主窗口 | `desktop/src/main/windows/main-window.ts` | 单 `BrowserWindow`，无标签页/view |
| Electron preload | `desktop/src/preload/index.ts` | 仅暴露 `octopDesktop` 窗口/更新/会话能力 |
| IPC 契约 | `desktop/src/shared/ipc-contract.ts` | 无浏览器相关通道 |

## 3. 总体架构

```
Octop Electron
├─ 主进程
│  ├─ BrowserTabManager          # WebContentsView 多标签管理
│  ├─ FrameBridge                # 帧注册、DOM/fetch 路由、下载观察、新窗口转达
│  ├─ OctopBackendProxy          # 现有：用户/会话/模型配置等仍走后端
│  └─ ipc-contract               # 新增浏览器/帧桥通道
├─ Preload
│  ├─ 主帧（Dashboard）           # 暴露 octopDesktop.browserHost
│  └─ 浏览器标签页                # 注入 dom-agent、lockdown、帧注册
└─ Dashboard 渲染进程
   ├─ LocalBrowserPage           # 标签栏 + 地址栏 + view 占位
   ├─ WebSkillLocalHost          # 直接装配 Chatbot/Console
   └─ RemoteBrowserPage          # 保留：服务器模式推流
```

核心原则：**WebSkill 上游包不修改**。Octop 新增代码只负责：
- 提供 `ChatbotHostAdapter` 所需端口；
- 把 `PageAgentTransport` 的底层通道从 WebSocket 换成 Electron IPC；
- 把「浏览器标签页」概念映射为 `WebContentsView`。

## 4. 详细设计

### 4.1 主进程：浏览器标签页管理（BrowserTabManager）

使用 `WebContentsView`（Electron ≥ 30）实现多标签。每个标签页一个 view，主进程维护生命周期。

#### 4.1.1 数据结构

```ts
// desktop/src/main/browser/tabs.ts
export interface BrowserTab {
  id: string;              // 内部 tab id，如 "tab-01J..."
  url: string;
  title: string;
  active: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  isLoading: boolean;
}

export interface BrowserTabCreateOptions {
  url: string;
  active?: boolean;        // 是否立即切换过去，默认 true
  openerId?: string;       // 来源标签，用于 window.open 追踪
}
```

#### 4.1.2 管理器接口

```ts
export class BrowserTabManager {
  constructor(private readonly hostWindow: BrowserWindow) {}

  list(): BrowserTab[];
  activeTabId(): string | undefined;

  create(options: BrowserTabCreateOptions): string;
  close(tabId: string): void;
  activate(tabId: string): void;

  navigate(tabId: string, url: string): void;
  goBack(tabId: string): void;
  goForward(tabId: string): void;
  reload(tabId: string): void;

  /** 同步某个 view 的可见区域；由渲染进程布局变化时调用 */
  setViewportBounds(tabId: string, bounds: Rectangle): void;

  /** 供 FrameBridge 使用 */
  getWebContents(tabId: string): WebContents | undefined;

  onChanged(listener: () => void): () => void;
  onTabEvent(listener: (event: BrowserTabEvent) => void): () => void;
}

export type BrowserTabEvent =
  | { kind: "created"; tab: BrowserTab }
  | { kind: "updated"; tab: BrowserTab }
  | { kind: "closed"; tabId: string }
  | { kind: "activated"; tabId: string }
  | { kind: "opened-by-page"; openerId: string; url: string; tabId: string };
```

#### 4.1.3 新窗口处理

在创建每个 `WebContentsView` 时安装 `setWindowOpenHandler`：

```ts
view.webContents.setWindowOpenHandler(({ url }) => {
  // 真实浏览器标签页里所有新窗口都转为新标签
  const tabId = manager.create({ url, active: true, openerId: tabId });
  return { action: "deny" };
});
```

这与扩展的 `window.open` lockdown 行为一致：用户点击链接后自动切到新网页，且不会弹出系统浏览器窗口。

#### 4.1.4 CORS 说明

真实浏览器标签页加载 `https://` 网站时，**导航不受 CORS 限制**。需要处理的两类跨域场景：

1. **Dashboard 自身需要请求外部资源**：通过主进程 `net.request` 代理，或走帧桥在目标标签页上下文执行；
2. **WebSkill 读取链接文档/跨域 fetch**：通过 `FrameBridge.fetchInTab()` 在对应标签页执行，自动携带该站点的 cookie/登录态。

因此保持 `webSecurity: true`，不关闭安全策略。

### 4.2 主进程：帧桥（FrameBridge）

借鉴 `ccs-framework/electron/frame-bridge.ts`，把扩展的 `chrome.tabs.sendMessage` + service worker 替换为 Electron IPC。

#### 4.2.1 职责

- 标签页/帧注册与句柄发放；
- `perceive` / `execute` / `capture-image` 路由到目标标签页；
- 跨域 `fetch` 路由到目标标签页执行；
- 下载观察窗（`will-download` 计数）；
- 新窗口事件转达给 Dashboard。

#### 4.2.2 接口

```ts
// desktop/src/main/browser/frame-bridge.ts
export interface FrameBridge {
  /** 主帧（Dashboard）调用：对某个标签页执行 PageAgent 请求 */
  domRequest(tabId: string, request: PageAgentRequest): Promise<PageAgentReply>;

  /** 主帧调用：在指定标签页内 fetch，带页面登录态 */
  fetchInTab(tabId: string, url: string, init?: RequestInit): Promise<{
    status: number;
    headers: Record<string, string>;
    body: string; // base64
  }>;

  /** 下载观察窗 */
  openDownloadWindow(): string;
  settleDownloadWindow(token: string, timeoutMs: number): Promise<number>;

  /** 标签页 preload 调用：注册/更新帧信息 */
  announce(tabId: string, frameInfo: FrameInfo): void;
}

export interface FrameInfo {
  frameKey: string;       // "frm-<frameTreeNodeId>"
  url: string;
  title: string;
  origin: string;
  isMainFrame: boolean;
}
```

#### 4.2.3 安全闸门

与 `ccs-framework` 一致：
- `domRequest` / `fetchInTab` / 下载观察只接受 **Dashboard 主帧** 的 IPC；
- 标签页 preload 只接受 **对应标签页子帧** 的消息；
- 主进程校验 `event.sender` 与 `tabId` 的归属关系，防止跨标签页冒用。

### 4.3 Preload 改造

#### 4.3.1 主帧（Dashboard）

在现有 `desktop/src/preload/index.ts` 中新增 `browserHost` 桥：

```ts
// desktop/src/preload/index.ts（追加）
const browserHost = {
  tabs: {
    list: () => ipcRenderer.invoke(IPC_CHANNELS.browserTabsList),
    create: (options: BrowserTabCreateOptions) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsCreate, options),
    close: (tabId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsClose, tabId),
    activate: (tabId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsActivate, tabId),
    navigate: (tabId: string, url: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsNavigate, { tabId, url }),
    goBack: (tabId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsGoBack, tabId),
    goForward: (tabId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsGoForward, tabId),
    reload: (tabId: string) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserTabsReload, tabId),
    setViewportBounds: (tabId: string, bounds: Rectangle) =>
      ipcRenderer.send(IPC_CHANNELS.browserTabsSetBounds, { tabId, bounds }),
    onChanged: (cb: () => void) =>
      subscribe(IPC_CHANNELS.browserTabsChanged, cb),
    onEvent: (cb: (event: BrowserTabEvent) => void) =>
      subscribe(IPC_CHANNELS.browserTabsEvent, cb),
  },
  frame: {
    domRequest: (tabId: string, request: PageAgentRequest) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserFrameDom, { tabId, request }),
    fetchInTab: (tabId: string, url: string, init?: RequestInit) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserFrameFetch, { tabId, url, init }),
  },
  downloads: {
    openWindow: () =>
      ipcRenderer.invoke(IPC_CHANNELS.browserDownloadsOpen),
    settleWindow: (token: string, timeoutMs: number) =>
      ipcRenderer.invoke(IPC_CHANNELS.browserDownloadsSettle, { token, timeoutMs }),
  },
};

contextBridge.exposeInMainWorld("octopDesktop", {
  ...existingBridge,
  browserHost,
});
```

#### 4.3.2 浏览器标签页 preload

新增 `desktop/src/preload/browser-tab.ts`，注入到每个 `WebContentsView`：

```ts
// desktop/src/preload/browser-tab.ts
import { ipcRenderer, webFrame } from "electron";
import domAgentScript from "./dom-agent.js?raw";      // 来自 WebSkill 扩展产物
import lockdownScript from "./lockdown.js?raw";       // window.open 拦截

// 1. 在主世界注入页面代理和 lockdown
webFrame.executeJavaScript(domAgentScript);
webFrame.executeJavaScript(lockdownScript);

// 2. 向主进程注册本帧
ipcRenderer.send(IPC_CHANNELS.browserFrameAnnounce, {
  url: location.href,
  title: document.title,
  origin: location.origin,
  isMainFrame: process.isMainFrame,
});

// 3. 监听主进程投来的 DOM/fetch 执行指令，转给主世界 agent
// 4. 监听主世界 lockdown 的新窗口事件，转给主进程开新标签
```

`dom-agent.js` 可直接复用 `web-skill-sdk/examples/browser-extension/dist/content.js` 中的页面代理部分，或从 `@webskill/sdk/browser` 构建产物提取。

### 4.4 Dashboard：本地浏览器页面

新增 `dashboard/src/pages/Control/LocalBrowser/index.tsx`，与现有 `RemoteBrowserPage` 并列。

```tsx
export default function LocalBrowserPage() {
  const tabs = useBrowserTabs();          // 通过 octopDesktop.browserHost.tabs
  const activeTab = tabs.find(t => t.active);

  return (
    <div className={styles.page}>
      <ChromeTabBar tabs={tabs} onChange={activate} onClose={close} onNewTab={create} />
      <AddressBar tab={activeTab} onNavigate={navigate} />
      <div ref={viewportRef} className={styles.viewport}>
        {/* WebContentsView 由主进程渲染，此处仅占位并同步 bounds */}
      </div>
      <WebSkillLocalHost tabId={activeTab?.id} />
    </div>
  );
}
```

视口同步：

```ts
useEffect(() => {
  if (!activeTab || !viewportRef.current) return;
  const rect = viewportRef.current.getBoundingClientRect();
  window.octopDesktop!.browserHost!.tabs.setViewportBounds(activeTab.id, {
    x: rect.x, y: rect.y, width: rect.width, height: rect.height,
  });
}, [activeTab, viewportRef.current, tabs]);
```

### 4.5 WebSkill 本地宿主装配

新增 `dashboard/src/webskill-host/`（或复用 `dashboard/webskill/` 做环境分支），直接渲染 `<Chatbot>` 和 `<Console>`。

#### 4.5.1 Transport 替换

```ts
// dashboard/src/webskill-host/transport.ts
import type { PageAgentTransport, PageAgentRequest, PageAgentReply } from "@webskill/sdk/browser";

export class OctopLocalFrameTransport implements PageAgentTransport {
  constructor(private readonly resolveTabId: () => string | undefined) {}

  async send(request: PageAgentRequest): Promise<PageAgentReply> {
    const tabId = this.resolveTabId();
    if (!tabId) throw new Error("No browser tab is bound");
    return window.octopDesktop!.browserHost!.frame.domRequest(tabId, request);
  }
}
```

#### 4.5.2 TabWorksetPort 替换

```ts
// dashboard/src/webskill-host/tabWorkset.ts
export class OctopLocalTabWorksetPort implements TabWorksetPort {
  async list(): Promise<readonly WorksetTab[]> {
    const tabs = await window.octopDesktop!.browserHost!.tabs.list();
    return tabs.map(t => ({ handle: t.id, title: t.title, url: t.url }));
  }

  async focus(handle: string): Promise<void> {
    // 只改 Agent 焦点，不改变用户可见标签（SC-3）
    this.focusedHandle = handle;
  }

  async close(handle: string): Promise<void> {
    await window.octopDesktop!.browserHost!.tabs.close(handle);
  }

  async admit(opened: OpenedPageInfo): Promise<string> {
    const tabId = await window.octopDesktop!.browserHost!.tabs.create({
      url: opened.url!, active: false,
    });
    return tabId;
  }

  async reset(): Promise<void> {
    this.focusedHandle = undefined;
  }
}
```

#### 4.5.3 Adapter 装配

```ts
// dashboard/src/webskill-host/adapter.ts
export function createLocalHostAdapter(runtime: WebSkillRuntime): ChatbotHostAdapter {
  const targets = createRemoteTargetRegistry();
  const transport = new OctopLocalFrameTransport(() => workset.focusedTabId() ?? activeTabId());
  const pagePerception = new PagePerceptionPolicy({
    scope: { include: ["body"] },
    reader: createRemotePerceptionReader({ transport, targets }),
    // ...
  });
  const pageActions = new PageActionPolicy({
    scope: { include: ["body"] },
    executor: createRemotePageActionExecutor({ transport, targets }),
    // ...
  });

  return {
    storage: runtime.storage,
    skillRoots: runtime.skillRoots,
    navigation: { openConsole, openConsoleTrace, openConsoleCandidate },
    pagePerception,
    pageActions,
    pageTabs: new TabWorksetPolicy({ port: new OctopLocalTabWorksetPort(), enabled: () => pageActions.enabled }),
    pagePerceptionPaging: { enabled: true, ...PERCEPTION_PAGING_DEFAULTS },
    linkedDocuments: createLocalLinkedDocumentReader(),
    downloads: createLocalDownloads(),
    docxExtractor: extractDocxText,
    xlsxExtractor: extractXlsxText,
    // ...其余与 browser-extension 的 adapter 一致
  };
}
```

### 4.6 模式切换

在 `dashboard/src/pages/Control/WebSkillBrowser/index.tsx` 或路由层做环境分支：

```tsx
export default function WebSkillBrowserPage() {
  if (isDesktopShell()) {
    return <LocalBrowserPage enhanced />;
  }
  return <RemoteBrowserPage embedded enhanced />;
}
```

「控制 → 浏览器」普通页面同理：

```tsx
export default function BrowserPage() {
  if (isDesktopShell()) {
    return <LocalBrowserPage />;
  }
  return <RemoteBrowserPage />;
}
```

## 5. 接口定义汇总

### 5.1 IPC 通道（新增）

```ts
// desktop/src/shared/ipc-contract.ts（追加）
export const IPC_CHANNELS = {
  // ...现有通道
  browserTabsList: "octop:browser:tabs:list",
  browserTabsCreate: "octop:browser:tabs:create",
  browserTabsClose: "octop:browser:tabs:close",
  browserTabsActivate: "octop:browser:tabs:activate",
  browserTabsNavigate: "octop:browser:tabs:navigate",
  browserTabsGoBack: "octop:browser:tabs:go-back",
  browserTabsGoForward: "octop:browser:tabs:go-forward",
  browserTabsReload: "octop:browser:tabs:reload",
  browserTabsSetBounds: "octop:browser:tabs:set-bounds",
  browserTabsChanged: "octop:browser:tabs:changed",
  browserTabsEvent: "octop:browser:tabs:event",
  browserFrameDom: "octop:browser:frame:dom",
  browserFrameFetch: "octop:browser:frame:fetch",
  browserFrameAnnounce: "octop:browser:frame:announce",
  browserDownloadsOpen: "octop:browser:downloads:open",
  browserDownloadsSettle: "octop:browser:downloads:settle",
} as const;
```

### 5.2 渲染进程桥类型

```ts
// dashboard/src/utils/desktopBridge.ts（追加）
export interface DesktopBrowserHost {
  tabs: {
    list(): Promise<BrowserTab[]>;
    create(options: BrowserTabCreateOptions): Promise<string>;
    close(tabId: string): Promise<void>;
    activate(tabId: string): Promise<void>;
    navigate(tabId: string, url: string): Promise<void>;
    goBack(tabId: string): Promise<void>;
    goForward(tabId: string): Promise<void>;
    reload(tabId: string): Promise<void>;
    setViewportBounds(tabId: string, bounds: Rectangle): void;
    onChanged(callback: () => void): () => void;
    onEvent(callback: (event: BrowserTabEvent) => void): () => void;
  };
  frame: {
    domRequest(tabId: string, request: PageAgentRequest): Promise<PageAgentReply>;
    fetchInTab(tabId: string, url: string, init?: RequestInit): Promise<TabFetchResult>;
  };
  downloads: {
    openWindow(): Promise<string>;
    settleWindow(token: string, timeoutMs: number): Promise<number>;
  };
}

export interface OctopDesktopBridge {
  // ...现有字段
  browserHost?: DesktopBrowserHost;
}
```

## 6. 文件级改造清单（实际落地）

### 6.1 新增文件

| 文件 | 说明 |
|------|------|
| `desktop/src/main/browser/tabs.ts` | `BrowserTabManager`（含 `normalizeTabUrl` / `externalAppUrl`） |
| `desktop/src/main/browser/tabs.test.ts` | 管理器单测（18 项） |
| `desktop/src/main/browser/frame-bridge.ts` | `FrameBridge`：PageAgent 转发、回包归属校验、`fetchInTab` |
| `desktop/src/main/browser/frame-bridge.test.ts` | 帧桥单测（11 项） |
| `desktop/src/main/browser/downloads.ts` | `DownloadTracker`：`will-download`、重名保护、读取 |
| `desktop/src/main/browser/downloads.test.ts` | 下载单测（7 项） |
| `desktop/src/main/browser/picked-files.ts` | 页面 `attach` 的原生文件选择结果读取（10 MB/文件、28 MB 总量） |
| `desktop/src/main/browser/picked-files.test.ts` | 读取与限额单测（5 项） |
| `desktop/src/main/storage/client-settings.test.ts` | `browserMode` 默认值/持久化/非法值单测（4 项） |
| `desktop/src/preload/browser-tab.ts` | 标签页 preload：SDK PageAgentHandler + 文件选择 |
| `dashboard/src/pages/Control/LocalBrowser/index.tsx` | 本地浏览器页面 |
| `dashboard/src/pages/Control/LocalBrowser/index.module.less` | 样式 |
| `dashboard/src/hooks/useBrowserTabs.ts` | 标签页状态订阅与操作 |
| `dashboard/src/hooks/useBrowserTabs.test.ts` | 上述 hook 单测（6 项） |
| `dashboard/src/hooks/useDesktopBrowserMode.ts` | 本地/远程模式解析（读 `ClientSettings.browserMode`） |
| `dashboard/src/hooks/useDesktopBrowserMode.test.ts` | 模式切换单测（5 项） |
| `dashboard/webskill/src/localHost.ts` | iframe 侧访问 `octopDesktop.browserHost` |
| `dashboard/webskill/src/localHost.test.ts` | 本地 transport / tab port / 下载 / 链接文档单测（15 项） |
| `dashboard/webskill/src/localDownloads.ts` | 本地下载观察窗与读取端口 |
| `dashboard/webskill/src/localLinkedDocuments.ts` | 标签页内跨域文档读取 |
| `dashboard/webskill/src/seedSkills.ts` | 内置技能播种：把 `skills/builtin/*` 写进 OPFS `/builtin` |
| `dashboard/webskill/src/seedSkills.test.ts` | 播种/幂等/墓碑/用户改动保留单测（4 项） |
| `dashboard/webskill/skills/builtin/*` | 随包发布的四个默认技能（authored-bulletin/screen/slides、dwg-view） |
| `dashboard/src/hooks/useDefaultBrowserUrl.ts` | 读取 config.json `desktop_browser_url` 作为首个标签页 |
| `dashboard/src/hooks/useDefaultBrowserUrl.test.ts` | URL 解析/回退单测（3 项） |

### 6.2 修改文件

| 文件 | 改造内容 |
|------|---------|
| `desktop/src/shared/ipc-contract.ts` | 浏览器/帧桥/下载 IPC 通道；`ClientSettings.browserMode` |
| `desktop/src/main/storage/client-settings.ts` | `browserMode` 默认值与读写校验 |
| `desktop/src/main/index.ts` | 浏览器 IPC 注册、`BrowserTabManager`/`FrameBridge`/`DownloadTracker` 装配、`OCTOP_DESKTOP_BROWSER_MODE` 覆盖、标签页文件选择、外部协议交系统 |
| `src/octop/config.py` | 新增 `desktop_browser_url`（config.json 可配，默认值见 `DEFAULT_DESKTOP_BROWSER_URL`；`OCTOP_DESKTOP_BROWSER_URL` 覆盖） |
| `src/octop/api/routers/settings.py` | 新增 `GET /api/settings/desktop-browser` |
| `desktop/src/preload/index.ts` | 暴露 `octopDesktop.browserHost`（tabs / frame / downloads） |
| `desktop/src/electron.vite.config.ts` | 新增 `browser-tab` preload 入口，`@webskill/sdk` 打进 preload |
| `desktop/src/package.json`、`desktop/src/pnpm-lock.yaml` | `@webskill/sdk`（devDependency，由 preload 打包） |
| `dashboard/src/utils/desktopBridge.ts` | `DesktopBrowserHost`、`DesktopBrowserMode`、`browserMode` 类型 |
| `dashboard/src/pages/Control/WebSkillBrowser/index.tsx` | 桌面端本地/远程模式分支与回退入口 |
| `dashboard/src/pages/Control/LocalBrowser/index.tsx` | 地址栏"远程浏览器"回退按钮 |
| `dashboard/src/components/WebSkillFrame.tsx` | `init` 携带 `localTabId`，新增 `local-tab` 消息 |
| `dashboard/src/locales/en.json`、`dashboard/src/locales/zh.json` | 本地浏览器与模式切换文案 |
| `dashboard/vite.config.ts` | dev 模式提供 `webskill/` 资产；`/api/browser-agent` 代理重写 Origin |
| `dashboard/webskill/src/bridge.ts` | `init.localTabId` 与 `local-tab` 消息 |
| `dashboard/webskill/src/host.ts` | 按环境装配本地/远端 transport、tab port、下载、链接文档 |
| `dashboard/webskill/src/pageTabs.ts` | `OctopLocalTabWorksetPort` + 本地标签观察器 |
| `dashboard/webskill/src/transport.ts` | 本地模式经 IPC 直连标签页 |

### 6.3 原设计列出、实际不需要改动

| 文件 | 原因 |
|------|------|
| `desktop/src/main/windows/main-window.ts` | `WebContentsView` 在创建时自带 `webPreferences`，主窗口无需区分 preload |
| `desktop/src/electron-builder.config.ts` | `files: ["out/**/*"]` 已覆盖新增 preload 产物（该文件当前改动来自 bundled expert 工作，与本功能无关） |
| `dashboard/src/routes/index.tsx` | 复用 `/browser` 路由，无需新增 |
| `dashboard/src/pages/Control/RemoteBrowser/index.tsx` | 零改动，仅作为回退/服务器模式 |
| `dashboard/src/webskill-host/*` | 未采用（见 §12.1） |
| `desktop/src/preload/dom-agent.js`、`lockdown.js` | 未采用（见 §12.2） |

### 6.4 不修改的上游包

- `@webskill/sdk`
- `@webskill/chatbot`
- `@webskill/console`

## 7. 兼容性与回退

1. **服务器模式完全保留**：现有 `RemoteBrowserPage`、`WebSkillFrame`、`OctopPageAgentTransport`、后端 `/api/browser-agent/ws` 均不改动；
2. **桌面端默认启用本地模式**：通过 `isDesktopShell()` + 客户端设置 `browserMode`（默认 `local`）检测；
3. **灰度开关**：桌面壳 `ClientSettings.browserMode`（`desktop-settings.json`）由 `octopDesktop.clientSettings` 读写，Dashboard 的 `useDesktopBrowserMode` 据此选择本地/远程；另支持 `OCTOP_DESKTOP_BROWSER_MODE=remote` 环境变量强制回退，无需改代码。注意：因 `config.json` 由后端读取、桌面壳无法据此切换，开关放在客户端设置而非 `config.json`；
4. **数据隔离**：本地模式的 WebSkill 配置仍存于 `localStorage` / OPFS，与服务器模式互不影响。

## 8. 安全设计

1. **webSecurity 保持开启**：真实浏览器标签页不关闭安全策略；
2. **preload 最小权限**：标签页 preload 不暴露 Node.js 能力，仅通过 IPC 与主进程帧桥通信；
3. **主进程 IPC 校验**：所有 `domRequest` / `fetchInTab` 校验 `event.sender` 属于 Dashboard 主窗口；所有标签页消息校验 `webContents` 属于对应 `tabId`；
4. **新窗口白名单**：`setWindowOpenHandler` 把 `http:` / `https:` 转为新标签页；`mailto:` / `tel:` / 自定义应用协议经 `shell.openExternal` 交系统处理；`javascript:` / `data:` / `file:` / `about:` / `blob:` / `octop:` 直接拒绝；
5. **下载目录**：默认使用系统下载目录，同名自动加序号，不静默覆盖。

## 9. 测试计划

### 9.1 主进程单元测试（已实现）

- `BrowserTabManager`：创建/切换/关闭/导航/新窗口转标签/external 协议/bounds/显隐/上限；
- `FrameBridge`：DOM 请求路由、回包来源校验、超时、fetch 路由；
- `DownloadTracker`：观察窗、重名保护、读取与限额；
- `readPickedFiles`：页面 `attach` 的文件读取与限额。

### 9.2 Dashboard 单元测试（已实现）

- `useBrowserTabs`：标签页状态同步与操作；
- `useDesktopBrowserMode`：本地/远程模式解析与持久化；
- WebSkill 子工程：`OctopLocalFrameTransport` 路由、`OctopLocalTabWorksetPort` 契约、本地下载与链接文档。

### 9.3 集成测试（桌面端）

| # | 场景 | 状态 |
|---|------|------|
| 1 | 访问 `https://cloud.tencent.com`，确认无 CORS 错误 | ✅ 已实测 |
| 2 | 点击 `target=_blank` 链接，新标签打开并自动激活 | ✅ 已实测 |
| 3 | 输入「点击登录按钮」，模型感知并操作当前标签页 | ⏳ 待模型环境验证（IPC/PageAgent 链路已单独实测：perceive 226 targets、execute 点击成功） |
| 4 | 触发文件下载，下载观察窗能捕获 | ✅ 已实测（含重名 `report (1).txt`） |
| 5 | 切换至服务器模式，确认推流功能不受影响 | ⏳ 待起服务器模式实例回归（`RemoteBrowserPage` 零改动；桌面端可通过"远程浏览器"按钮/模式设置回退） |

## 10. 实施里程碑

| 阶段 | 内容 | 预估工作量 |
|------|------|-----------|
| M1 | 主进程 `BrowserTabManager` + 基础 IPC + 标签页 preload | 1 周 |
| M2 | `FrameBridge` + DOM/fetch 路由 + 新窗口 lockdown | 1~2 周 |
| M3 | Dashboard 本地浏览器页面 + WebSkill 宿主装配 | 1~2 周 |
| M4 | 下载、链接文档、权限、UI 打磨、测试 | 1 周 |
| 合计 | | **4~6 周** |

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `WebContentsView` API 版本差异 | 锁定 Electron ≥ 30；如版本不足，回退到 `BrowserView` |
| 某些网站 CSP 阻止注入脚本 | preload 在 `document_start` 注入，不受页面 CSP 影响；必要时调整 `webPreferences` |
| WebSkill 后端 API 与本地能力不对齐 | 本地模式只实现扩展已有能力；后端特有功能（如录制回放）保留在服务器模式 |
| 性能：多标签内存占用 | 默认限制最大标签数 8；非活动标签休眠**未实现**，列为后续优化 |

## 12. 实施说明（与本设计稿的差异）

> 本节记录实际落地的方案与验证结果；前文保留为设计基线，两者不一致时以本节为准。

### 12.1 WebSkill 宿主：保留 iframe，只替换 transport

本设计稿 §4.5 / §6.1 计划把 `@webskill/chatbot` 直接渲染进 Dashboard，并新增
`dashboard/src/webskill-host/`。实际实现改为**保留 `dashboard/webskill` 独立页面与
`WebSkillFrame` iframe**，在宿主装配层做环境分支：

- `dashboard/webskill/src/localHost.ts` 读取 `window.parent.octopDesktop.browserHost`；
  存在即本地模式，不存在即服务器模式。
- `dashboard/webskill/src/transport.ts`：本地模式把 `PageAgentTransport` 的
  `perceive` / `execute` / `capture-image` 直接投递到 `browserHost.frame.domRequest()`，
  不再连 `/api/browser-agent/ws`；未绑定标签页时明确报错，不回落后端。
- `dashboard/webskill/src/pageTabs.ts`：新增 `OctopLocalTabWorksetPort`（列表/焦点/关闭/
  收纳）。`focus()` 只改 Agent 焦点，不切换用户可见标签（SC-3）。
- `dashboard/webskill/src/localDownloads.ts` / `localLinkedDocuments.ts`：下载观察窗与
  链接文档读取改走主进程 IPC（`will-download` 计数、标签页内 `fetch` 携带站点登录态）。
- `dashboard/src/components/WebSkillFrame.tsx` 在 `init` 之外新增 `local-tab` 消息：
  用户切换标签页时更新 iframe 的当前标签，页面操作跟随用户所见。

这样上游 `@webskill/sdk`、`@webskill/chatbot`、`@webskill/console` 仍为零改动，也不需要
把 Node 依赖/shims 塞进 Dashboard 主包（`webskill/` 子工程本来就独立构建）。

### 12.2 标签页 preload：直接用 SDK Handler

不新增 `dom-agent.js` / `lockdown.js`：`desktop/src/preload/browser-tab.ts` 在标签页
preload（`contextIsolation: true`、`sandbox: false`）里直接
`createPageAgentHandler({ actionScope, document, pickFiles })`，只通过三个 IPC 通道与主进程
通信（请求 / 回包 / 文件选择），页面本身拿不到任何 Node 能力。`window.open` / `target=_blank`
由主进程 `setWindowOpenHandler` 统一处理：http(s) 转新标签页，`mailto:`/`tel:`/应用协议交
`shell.openExternal`，危险协议拒绝。页面 `attach` 动作会经主进程弹出原生文件选择框，读回
文件后转成 `File` 交给 SDK Handler。

### 12.3 主进程模块

| 文件 | 实现 |
|------|------|
| `desktop/src/main/browser/tabs.ts` | `BrowserTabManager`：`WebContentsView` 多标签、每标签 bounds、标签数上限 8、`setHostVisible()` 统一控制原生视图显隐、`ownsWebContents()` 供下载归属判定。不依赖 Electron 运行时，可单测。 |
| `desktop/src/main/browser/frame-bridge.ts` | 主进程只转发：校验 Dashboard 发送者、按 webContents 归属匹配回包、30s 超时、`fetchInTab()` 在标签页上下文执行带 cookie 的 fetch；同样可在 Node 下单测。 |
| `desktop/src/main/browser/downloads.ts` | `DownloadTracker`：监听 `will-download`、落到系统下载目录、重名自动 `report (1).txt`、`openWindow()/settleWindow()` 供 WebSkill 判定「本次操作触发了几个下载」、`list()/read()` 供读取文件。 |
| `desktop/src/main/browser/picked-files.ts` | 页面 `attach` 的原生文件选择读取：单文件 ≤ 10 MB、总量 ≤ 28 MB，按扩展名推断 MIME，base64 回传标签页。 |
| `desktop/src/main/index.ts` | 注册全部浏览器 IPC，校验 Dashboard/标签页来源；`OCTOP_DESKTOP_BROWSER_MODE` 可强制 `local`/`remote`；标签页/窗口销毁、页面 reload、渲染进程崩溃时隐藏并回收原生视图。 |
| `desktop/src/preload/index.ts` | `octopDesktop.browserHost`：tabs / frame / downloads 三组方法。 |

安全闸门：`domRequest`、`fetchInTab`、下载读取只接受 Dashboard 主窗口的 IPC；标签页回包
必须来自发起请求的那个 webContents；导航只允许 http/https。

### 12.4 Dashboard

- `dashboard/src/pages/Control/LocalBrowser/index.tsx`：标签栏 + 地址栏 + 原生视图占位 +
  `WebSkillFrame`。布局与远程浏览器一致（去掉标题/描述，AI 面板与浏览器列并列、独占整列），
  `ResizeObserver` 监听视口元素本身，拖动分隔条时原生视图随之缩放；离开页面时
  `setVisible(false)`。
- `dashboard/src/hooks/useBrowserTabs.ts`：标签页状态订阅、首个标签自动创建（等
  `desktop_browser_url` 就绪后再建，避免先用兜底地址）、错误回调
  （错误用页面内 `Alert` 展示，因为原生视图会盖住 antd toast）。
- `dashboard/src/hooks/useDefaultBrowserUrl.ts`：从 `GET /api/settings/desktop-browser`
  读取 config.json 的 `desktop_browser_url`。
- `dashboard/src/hooks/useDesktopBrowserMode.ts`：读取 `ClientSettings.browserMode`；
  切换立即生效并持久化，供"本地/远程"回退使用。
- `dashboard/src/components/AssistantFrame/AssistantFrame.tsx`：右侧停靠宽度上限改为
  "窗口宽度 - 边距"，不再给浏览器列保留最小宽度，也不再封顶 720px；从 localStorage
  恢复的旧宽度会在加载/窗口 resize 时夹到当前窗口内，避免大屏遗留的宽度把小窗口里的
  浏览器列挤成 0。
- 打包：`electron-builder` 的 `extraResources` 把最新 `src/octop/dashboard` 复制到
  `resources/dashboard`；`resolveDashboardDir` 优先使用它，再回退到 portable runtime
  里的旧副本。这样安装版的前端与 shell 同版本，不需要为了前端改动重建绿色运行时。
- `desktop/src/main/index.ts`：Dashboard 首次 `did-finish-load` 后把页面缩放重置为
  100%（Chromium 会持久化每个 origin 的缩放）；`browserTabsSetBounds` 用
  `webContents.getZoomFactor()` 把渲染进程上报的 CSS 像素换算成原生 DIP，避免用户
  在会话内缩放后浏览器视图变大/错位；新建标签页也会重置为该站点的 100% 缩放。
- `dashboard/src/pages/Control/WebSkillBrowser/index.tsx`：桌面端按模式渲染本地浏览器或
  `RemoteBrowserPage`；两种模式的"本地/远程"切换按钮都放在浏览器地址栏最右侧
  （远程页通过 `RemoteBrowserPage.addressBarExtraEnd` 注入，不再有额外顶栏），
  `OCTOP_DESKTOP_BROWSER_MODE=remote` 可强制回退。切换时先用主题色遮住 AI 面板，
  等 WebSkill iframe 真正绘制完成后再显示，避免白屏闪动。
- `dashboard/webskill/src/seedSkills.ts` + `dashboard/webskill/skills/builtin/`：首启把四个
  默认技能写入 OPFS `/builtin`；`skillRoots`、控制台后端与治理入口都包含该根，控制台因此
  能列出并启用它们；`webskill/vite.config.ts` 负责把技能资产拷进构建产物。
- `dashboard/vite.config.ts`：`pnpm dev` 时由 Vite 直接提供
  `src/octop/dashboard/webskill/`（否则 iframe 在开发模式 404）；`/api/browser-agent`
  代理重写 `Origin`，满足后端的同源校验。

### 12.5 与设计稿的其它差异

| 设计稿 | 实际 |
|--------|------|
| `frame.announce` / `FrameInfo` 帧注册 | 不需要：每个标签页只有一个 PageAgent Handler，主进程按标签页路由即可 |
| `FrameBridge.openDownloadWindow()` | 由独立的 `DownloadTracker` 承担，并通过 `browser.host.downloads` 暴露 |
| `dashboard/src/hooks/useBrowserTabs.ts` | 已实现，并带 Vitest 单测 |
| 本地下载读取 | 主进程读取系统下载目录文件（上限 32 MB），不下发任意路径 |
| 录制/回放、技能草稿 | 仍走后端会话，本地模式暂不支持（沿用 §11 的取舍） |
| `config.json: desktop.browserMode` | 改为桌面壳 `ClientSettings.browserMode` + `OCTOP_DESKTOP_BROWSER_MODE` 覆盖（后端配置桌面壳读不到） |
| 新窗口"其他协议交系统浏览器" | 仅放行 `mailto:` / `tel:` / 自定义应用协议；`javascript:` / `data:` / `file:` / `about:` / `blob:` / `octop:` 拒绝 |
| 页面 `attach` | 已支持：主进程原生文件框 → base64 → 标签页 `File` → SDK Handler（单文件 ≤ 10 MB、总量 ≤ 28 MB） |
| 本地浏览器默认网址 | 改为 config.json `desktop_browser_url`（`GET /api/settings/desktop-browser`；文件未配置时用代码默认值 `DEFAULT_DESKTOP_BROWSER_URL`，`OCTOP_DESKTOP_BROWSER_URL` 可覆盖），首个标签页等待配置就绪后再创建 |
| 默认技能 | 随包发布四个内置技能（`webskill/skills/builtin`），首启播种到 OPFS `/builtin`，并加入 `skillRoots` 与控制台根 |
| 本地/远程界面一致性 | 本地页去掉标题/描述，AI 面板与浏览器列并列；`AssistantFrame` 取消浏览器列最小宽度与 720px 面板上限 |
| 页面缩放 | Dashboard 启动时重置为 100%；会话内缩放通过 `zoomFactor` 换算 bounds；新标签页重置站点缩放，避免原生视图与占位框错位 |
| 本地/远程切换闪动 | AI 面板 iframe 重建时会先白屏再绘制；`WebSkillFrame` 在 iframe 里立即写入主题背景，并用主题色遮罩到 `ready` + 首帧后再揭开，切换不再出现白块 |

### 12.6 测试与验证

- 主进程单测（`node --test`，共 46 项）：`main/browser/tabs.test.ts`、`frame-bridge.test.ts`、
  `downloads.test.ts`、`picked-files.test.ts`、`main/storage/client-settings.test.ts` ——
  覆盖创建/切换/关闭/导航、`window.open` 转标签与外部协议、bounds 与显隐/缩放换算、
  标签上限、回包来源校验、超时、下载观察窗与重名保护、页面 attach 文件读取与限额、
  浏览器模式设置。
- 桌面 bootstrap 单测（`node --test`，7 项）：`bootstrap-config.test.ts` 覆盖首次写入、
  部署默认值回填（不覆盖用户值）、坏 JSON 不动；`bootstrap-deployment.test.ts` 覆盖
  `desktop_browser_url` 随品牌配置合并，以及品牌构建自动识别 `resources/bootstrap/config.json`。
- Dashboard 单测（Vitest）：`src/hooks/useBrowserTabs.test.ts`、
  `src/hooks/useDesktopBrowserMode.test.ts`、`src/hooks/useDefaultBrowserUrl.test.ts`；
  WebSkill 子工程：`webskill/src/localHost.test.ts`（本地 transport/tab port/下载/链接文档）
  与 `webskill/src/seedSkills.test.ts`（内置技能播种/幂等/墓碑）。
- 后端单测：`tests/unit/test_config.py` 覆盖 `desktop_browser_url` 的默认值、文件配置、
  环境变量覆盖与非法值回退。
- 手动 E2E（桌面端静态构建 + CDP）：本地标签页加载站点（含 config.json 指定的起始页）、
  `perceive`/`execute`（点击 `target=_blank` 链接自动开新标签并激活）、切换标签后 iframe
  收到 `local-tab`、`fetchInTab` 带登录态、`will-download` 观察窗与文件读取、离开/返回
  浏览器页时原生视图正确隐藏/恢复、拖动分隔条时浏览器视口与原生视图同步缩放、
  本地/远程来回切换后 AI 面板不白屏、控制台列出四个内置技能。
- 打包后自检：`desktop/src/scripts/verify-package.mjs` 校验
  `resources/dashboard/index.html` 与 `src/octop/dashboard` 完全一致、WebSkill host 存在、
  四个内置技能目录齐全；`package` / `package:dir` / `scripts\package-win.cmd` 会自动执行。
  这补上了“单测通过但安装包复制了旧 Dashboard”的盲区。
- 仍需在有模型的环境补测：§9.3 第 3 条（自然语言驱动的页面操作）；服务器模式实例回归为
  §9.3 第 5 条。

### 12.7 Windows 构建的默认起始页

品牌构建（Windows 安装包）的默认起始页配置在
`desktop/src/resources/bootstrap/config.json`：

```json
{
  "database": { "driver": "sqlite" },
  "desktop_browser_url": "https://jijian.huawei.com/"
}
```

链路：

1. 品牌构建仓库里 `resources/bootstrap/` 同时存在 `config.json`、`users.json`、
   `models.json`（后两个 gitignore）时，`pnpm package` 会自动选它，无需额外环境变量；
   仍可用 `OCTOP_DESKTOP_BOOTSTRAP_CONFIG` 显式覆盖。`prepareBootstrapDeployment` 会把
   该文件与 users/models 合并成 `release/bootstrap/bootstrap.json`
   （`desktop_browser_url` 原样保留）；
2. 首次启动：主进程把它写入 `<OCTOP_HOME>/config.json`；
3. 已有 `<OCTOP_HOME>/config.json` 的机器：只**补齐缺失的**`desktop_browser_url`，
   不覆盖用户已配置的值；
4. 后端 `load_config` 读取该键，Dashboard 通过 `GET /api/settings/desktop-browser`
   作为首个标签页地址。

因此运行中的机器要立即改地址，也可以直接编辑 `<OCTOP_HOME>/config.json`（本机为
`C:\Users\<user>\.octop\config.json`）后重启；改 `bootstrap/config.json` 只影响之后
打包/安装的 Windows 版本。

