# WebSkill 宿主接入对照（Octop / extension / demo）

> 状态：本地接入审计；不是生产支持声明。  
> 对照：`web-skill-sdk/examples/browser-extension/src/shared/assembly.ts`、`web-skill-site/src/demo/webskill/adapter.ts`、Octop `dashboard/webskill/src/host.ts`。

## 1. 已有能力

| 能力 | Extension | Demo | Octop | 备注 |
|---|---|---|---|---|
| pagePerception / pageActions | ✅ | ✅ | ✅ | Octop 后端补了 titled-card promotion |
| pagePerceptionPaging | ✅ | ✅ | ✅ | |
| pageTabs / target workset | ✅ | — | ✅ | Octop 新开 Tab 现在会等待并激活 |
| pageImage | ✅ remote | ✅ DOM capture | ✅ remote | |
| PDF / DOCX / XLSX / PPTX readers | ✅ | ✅ | ✅ | |
| XLSX table reader | ✅ | ✅ | ✅ | |
| DOCX/XLSX templates | ✅ | ✅ | ✅ | |
| documentSurface | ✅ | ✅ | ✅ | Octop 是内嵌 shell |
| linkedDocuments | ✅ content script | ✅ same-origin fetch | ✅ page-context fetch | 见 §3 |
| downloads | ✅ + consent | — | ✅ + consent | 见 §2 |
| pageAction consent | ✅ | ✅ | ✅ | 见 §2 |
| pageAction download signal | ✅ | ✅ | ✅ | 通过 `downloads-list` 差分 |
| pageAction UI / handoff | side panel | page overlay | server control owner | Octop 特有 |
| Console consent list/revoke | ✅ | ✅ | ✅ minimal | ConnectFacade 端点列表为空 |

## 2. 本轮补的接入

- `pageActionConsent`：origin + action，localStorage；SDK 因此显示“以后不再询问”
- `downloadConsent`：downloads + action，localStorage
- `downloadWatcher`：动作前读 `downloads-list`，settle 时轮询新 id，回传 `downloadCount`
- `linkedDocuments`：Octop 宿主不再在 WebSkill iframe 里直接 fetch；新增后端
  `/browser-agent/sessions/{id}/linked-document`，在受控页面上下文用 `credentials:"include"` 取件
- `ConnectFacade`：Console 连接页可列/撤销 page action 与 download consent；端点列表为空

## 3. Octop 特有边界

- linkedDocuments 当前只允许**同源** URL。extension 允许同注册域（含 `files.` 子域），Octop 未引入 PSL，因此先按更严格的同源执行。
- ConnectFacade 不接远程 MCP/WebMCP；端点列表为空，这是当前 R2C 设计。
- 远程页面图标、DWG、WebOffice 仍按 R2C 明确 unsupported，不是接入遗漏。
- `fill/select` 没有 demo 的预授权；与 extension 一致，仍逐个确认。
- 旧版已写入的 page action consent（无 `id`）不会被 Console 列出，但 recall 仍生效；新写入记录完整。

## 4. 验证

- `test_webskill_page_agent.py`：25 passed
- `pageActionConsent.test.ts`、`downloadConsent.test.ts`：3 passed
- WebSkill typecheck/build 通过
- 手动宿主复核：新 Tab 激活、工程管理/工作面移交计划读取、page action 记住、下载 consent 存储

> SDK 源码、Chatbot/Console 包版本未修改；仅修改 Octop 宿主与后端。
