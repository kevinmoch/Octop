# P1 实施与真实验证记录

> 状态：`candidate`，不表示生产支持或完整能力等价。主文档、独立模型、权限、ticket/grant、接管和单击闭环已实施；LNA 的真实用户拒绝路径仍未验证。

## 1. 实施边界

- 新菜单为“控制 → 浏览器”（位于“工作台”之后的独立入口），路由 `/browser`，复用 `browser` 权限和用户 profile；旧 `/workbench/browser` 保留 Octop 原有入口，不是同一宿主。
- 复用 Octop 的 BrowserViewer、画面流、接管和 octop-browser；不复制另一套浏览器工作区。
- WebSkill 使用独立 React 19 静态包，固定 `SDK 0.29.0 / Chatbot 0.26.0 / Console 0.22.5`。不修改或发布 SDK，不 fork Chatbot/Console。
- Chatbot 与 Console 在 Octop 右侧助手区域内切换，设置与运行记录不离开 Dashboard。
- PageAgent 仅支持主文档 body 与 `click/fill/select/scroll/back`；不支持任意 CDP、任意 JS、profile 或文件路径输入。
- grant 绑定用户、会话、profile、文档、target、frame、ref 和完整动作摘要；一次性、限时、可撤销。断线后旧 ref/document/grant 失效，同用户同 profile 可安全重建，不重放写操作，不杀共享 Chrome。
- 页面感知只使用一份 DOM 快照建 DOMBuilder、敏感子树过滤和 backendNodeId 映射，避免跨读取串号；真实单击使用 `DOM.resolveNode` + `DOM.requestNode` 和现有浏览器鼠标输入。
- 模型与 Token 使用 WebSkill 自己的配置、OPFS 和 trace；不占 Octop 的模型、连接器、技能或 Token 账本。
- 固定 SDK 的共享打包片段含 `node:module`。宿主构建使用明确的边界适配，只允许固定的 React 19 peer；其他 Node 模块加载明确失败，不增加通用 Node 能力。

### P1 评审修复记录（2026-11-17）

- **ref 隔离**：WebSkill 的 DOMBuilder 使用独立 `RefCache`，每次执行使用独立 action cache；动作通过 octop-browser 的现有接口执行。测试预置共享会话 ref，验证感知、执行和 grant 重放拒绝后共享映射保持不变。
- **handoff 写拦截**：复用 Octop 的精确 Origin 与 Fetch Metadata 校验；不合法 Origin 返回 403，不进入强制写操作。
- **单连接与安全复用**：attach 在 WebSocket accept 前完成；同一逻辑会话的第二个连接以 4001 拒绝。只有曾连接且当前断开的会话才可复用，未认领会话不会被另一连接抢占。
- **独立入口与真实 UI 验收**：保留用户要求的“工作台”之后独立“浏览器”菜单 `/browser`，不改为工作台子页；验收实际点击“控制 → 浏览器”，而非硬编码路由或验收结果。普通 LAN HTTP 也通过真实菜单点击验证拒绝说明。
- 修复后 31 项专项测试、真实 Chromium 单击闭环、真实 UI 与模型链路均复验通过；该轮 Octop 全量回归为 `5660 passed, 40 skipped`。

### P1 R2 复审修复记录（2026-11-17）

- **未认领会话 TTL**：新增 120 秒未认领 TTL，在 `create` 和 `issue_ticket` 前惰性清理；过期会话及其 ticket/grant 一并删除，不再复用未认领会话。达到 8 个未过期会话仍正确返回 `SESSION_LIMIT`，TTL 后可创建新会话，旧会话不能签发 ticket。
- **活动画面流保护**：画面流在 accept 前登记独立连接 ID，在 finally 中释放。TTL 不回收仍有活动画面流的未认领会话，避免用户尚未调用页面工具时被误删；逐一释放多个连接，旧连接不会误清新连接。该登记仅覆盖当前进程，不是 P2 的跨进程 lease。
- **行为与真实复验**：专项测试增至 34 项，新增容量恢复、活动流保护及真实 HTTP/WS 登记释放测试；真实 Chromium 单击闭环和真实 UI/模型/画面流链路均再次通过。

### P1 R3 复审修复记录（2026-11-17）

- **idle TTL**：会话按 `last_activity_at` 而非创建时间计算未认领 TTL；画面流接入/释放、PageAgent 接入和 ticket 签发刷新活动时间。活动超过 120 秒后断流，仍有完整的 120 秒恢复窗口，不会立即失效。
- **UI 失效会话恢复**：会话缓存和创建去重由 `useWebSkillAgentSession` 统一管理。画面流 ticket 遇到 404/410 时清除旧 ID、创建新会话并只重试一次；iframe 的旧 ticket 请求触发宿主换建，不把新会话 ticket 交给旧 iframe。非 404/410 不错误换建。
- **行为与真实复验**：34 项后端专项测试、4 项会话恢复 hook 测试和 3 项模型边界测试通过。真实 UI 验收注入一次 ticket 404，确认创建两个不同会话并恢复推流，随后模型回复、trace、主题切换和一次真实单击全部通过；仅豁免该注入接口的一条 404，其他控制台错误仍失败。
- 本机模型服务一度对直接 HTTP 请求也无响应，导致首次完整 UI 复验在模型阶段超时；服务恢复后未改生产代码，同一验收完整通过，不将该环境超时记为代码回归。

### 上游同步复验（2026-11-17，`ff078d3f` / Octop 1.0.3b6）

- `webskill-integration` 通过 merge commit `f0a5c193` 同步官方 `ff078d3f`，P1 功能分支保持不变。唯一冲突位于前端权限映射，保留官方 cloud 权限并加入 WebSkill browser 权限；相关 15 项权限测试通过。
- 同步后 34 项后端专项、7 项前端会话/模型测试均通过。真实 Chromium 证据更新为 `.artifacts/webskill-p1/688f24a665/result.json`；真实 UI 再次通过一次 404 换建、模型、trace、主题和确认单击。
- 主 Dashboard 单独构建会清空输出目录中的 WebSkill 子包，使 `/webskill/` 回退到主页面；完整 `make build-frontend` 会依次重建主包与 WebSkill 子包。首次因此产生的 iframe 初始化失败在完整构建后消除，不是上游代码冲突。
- 全量 `make all PYTEST_JOBS=4` 为 `5907 passed, 45 skipped`；Ruff、1310 个文件的格式检查和 mypy（682 个源码文件）均通过。新组合保持 `candidate`，不标记生产支持。

## 2. 已通过的验证

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 会话、grant、权限和适配层 | 34 项测试通过；覆盖一次性/过期、换值/重放、跨文档/跨用户、ref 隔离、handoff 来源校验、同会话双连接、未认领 idle TTL、活动流断开后完整恢复窗口、错误码白名单、无 browser 权限、审计脱敏 | 下方 pytest 命令 |
| UI 会话恢复 | 404/410 换建并重试一次、非失效错误不换建、并发创建去重均通过 | `useWebSkillAgentSession.test.ts` 4 项测试 |
| 真实 Chromium + HTTP/WS | 主文档感知、敏感子树过滤、单击一次、重放拒绝、人工导航接管、权限隔离、关闭逻辑会话不杀 Chrome、真实审计均通过 | `.artifacts/webskill-p1/688f24a665/result.json` |
| 真实 UI + 本机模型 | 实际菜单点击、一次 ticket 404 后换建并恢复推流、Chatbot 挂载、Console 模型配置、真实 `qwen3.5:9b` 回复、trace 中的 `llm.response` 与 `llm.usage`、主题实时切换、确认后单击一次均通过；无页面脚本错误 | `.artifacts/webskill-p1/ui/ui-result.json` |
| 新通路认证 | 画面流和 PageAgent 的 URL 参数只有尺寸及一次性 ticket，没有长期 `token` 参数 | 同上；旧工作台 JWT 仅限期兼容 |
| 普通远程 HTTP | 非回环 HTTP 的 `isSecureContext=false`，显示 HTTPS/OPFS/WebCrypto 拒绝说明，不挂载 WebSkill iframe | `.artifacts/webskill-p1/ui/insecure-result.json` |
| HTTPS | 临时自签 HTTPS 下的完整模型、trace 和确认单击通过 | `.artifacts/webskill-p1/ui/ui-result-https.json` |
| 真实鉴权端点 | 实际 HTTP 服务对兼容占位值返回 401；`LLM_REQUEST_FAILED`、请求一次、配置无占位值 | `cd dashboard/webskill && node scripts/model-auth-probe.mjs` |
| 独立包 | `npm ci` 干净安装、类型检查和 Vite 构建通过；React 19.2.8 单版本树，Console/Monaco 懒加载 | `dashboard/webskill/package-lock.json` |
| Octop 回归 | R3 修复后 `5663 passed, 40 skipped`；同步 `ff078d3f` 后 `5907 passed, 45 skipped`；Ruff、格式检查和 mypy 均通过 | `make all PYTEST_JOBS=4`，使用已存在的 uv |

## 3. 实际执行命令

```bash
cd /Users/mochunhui/Git/Octop
/Users/mochunhui/Git/octop-browser/.tools/uv/bin/uv run --no-sync pytest \
  tests/unit/api/test_webskill_sessions.py \
  tests/unit/api/test_webskill_page_agent.py \
  tests/unit/api/test_webskill_http.py -q
/Users/mochunhui/Git/octop-browser/.tools/uv/bin/uv run --no-sync python scripts/webskill-p1/real_acceptance.py
PATH="/Users/mochunhui/Git/octop-browser/.tools/uv/bin:$PATH" make all PYTEST_JOBS=4

cd /Users/mochunhui/Git/Octop/dashboard/webskill
npm ci --no-audit --no-fund
npm run build
node scripts/model-auth-probe.mjs

cd /Users/mochunhui/Git/Octop/dashboard
npx tsc -b
npm run lint
npm test -- src/pages/Control/WebSkillBrowser/model.test.ts src/pages/Control/WebSkillBrowser/useWebSkillAgentSession.test.ts
```

真实 UI 验收使用 `scripts/webskill-p1/ui_server.py` 的隔离测试账号和目录。先启动它，再执行 `scripts/webskill-p1/ui_acceptance.py`。验收会注入一次 ticket 404 并要求换建会话后完成全部原链路。网络专项分别设置 `WEBSKILL_P1_UI_URL`、`WEBSKILL_P1_INSECURE_URL`；非默认地址由执行环境提供，不写入源码。

## 4. 尚未通过或未验证

- **AC-49 的 LNA 用户拒绝尚未验证**：真实浏览器能识别 `local-network` / `loopback-network`，但本次 `Browser.setPermission(..., denied)` 后查询仍为 `prompt`；策略别名实验也没有形成可重复的“拒绝 → 模型失败”证据。因此不把 LNA 标为通过，不把权限查询成功当作拒绝路径。
- **临时证书环境错误**：HTTPS 验收中的自签证书导致 `sw.js` 注册报证书错误。应用、模型和单击链路通过，但正式受信 TLS、Service Worker/PWA 行为仍需在生产证书环境复验。
- **P2 未开放**：frame/OOPIF、独立 target 工作集、双指针完整语义、共享 profile 的跨写入者/跨进程 lease、容量和部署矩阵验收。
- **P3 未开放**：人工录制转换、候选审查链、ReplayRunner 授权执行、技能执行验收、PPT/DOCX/XLSX/大屏和 opaque viewer。
- 不把当前实现描述成扩展版的完全能力等价，也不把 candidate 组合描述成生产支持。

## 5. 发布边界

本阶段没有 npm 包发布、SDK 源码改动、提交、推送或 Octop 版本发布。后续变更继续走小 PR 和上游评审，不恢复旧控制器网关或专用 iframe 扩展。
