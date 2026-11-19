# P3 实施与真实验证记录

> 状态：🟡 P3 本地候选。人工候选、受限回放、两宿主技能执行、隔离 viewer、XLSX 与大屏已有真实证据；其余条目按本文件逐项验收，不是生产支持声明。
> R0 能力基线：原生技能学习是主路径；R1 已用真实 `qwen3.5:9b` 完成本地 candidate 闭环。固定模板录制/回放仅为 Octop 辅助。见 [design/01-capability-baseline.md](../design/01-capability-baseline.md) 与 [capability-inventory.json](../capability-inventory.json)。

## 1. 录制转候选（2026-11-18）

- 新增受限 `POST /api/browser-agent/sessions/{sessionId}/skill-drafts`，要求登录、`browser` 权限、精确 Origin 与 Fetch Metadata。会话归属由 registry 校验，recording 必须属于该会话对应 profile；跨用户或跨 profile 一律 404。
- 支持指定 `recordingId`，缺省时只选择当前用户 profile 的最新录制；录制必须已停止且含步骤。
- `RecordingStore` 的 manifest、events 与 steps 文件以流式 SHA-256 形成 `sourceDigest`；服务端审计只记录 recordingId、sourceDigest、draftDigest 与 evidence，不记录原始输入、页面正文或 URL query。
- 固定模板不调用模型，不包含 `GeneratedSkillDraft.steps`，不写入 CDP ref、selector 或坐标。输入值不进入候选；URL query 全部脱敏。固定模板不是 `ReplayRunner` 输入，也不代表执行授权。
- `draftDigest` 使用字段白名单、递归键排序、无空白 JSON 和 SHA-256。浏览器端使用公开 `parseSkillMarkdown/isValidSkillName` 校验 frontmatter 与名称，并拒绝未知字段、非有限数字、非 JSON 对象、附加文件、超限内容和摘要不一致。
- 客户端使用当前配置中的语言显示确认提示；取消确认不写候选。人工路径不把 recordingId 或 BrowserAgent sessionId 冒充 WebSkill 会话 ID。
- 人工与模型生成候选统一使用 `CandidateStore/createCandidateSink`；Console 使用公开 `createGovernanceFacade`，共用 `/managed` 与 `/chat` 存储根，未 fork 或修改 Chatbot/Console/SDK。

## 2. 行为验证

- `tests/unit/api/test_webskill_skill_drafts.py`：**6 passed**。使用真实 `RecordingStore` 验证模板确定性、值与 URL 脱敏、摘要范围、跨 profile 拒绝、空录制拒绝，以及真实 HTTP 接口的登录、权限、Origin、Fetch Metadata、归属和审计。
- 当前宿主桥与录制候选/回放/删除专项：**16 passed**，覆盖递归摘要、无 steps、确认与取消、非法 recordingId、父子窗口 session 边界及删除响应校验。主 Dashboard 与嵌套 WebSkill TypeScript 均通过。
- 最新后端候选、回放、删除与静态路由联合回归：**26 passed**；相关 Ruff 与 mypy 通过。
- 完整 Vitest 的历史结果为 **1838 passed / 27 failed**，另有 3 个既有 suite 加载失败，日志为 `.artifacts/webskill-p3/vitest-all.log`。失败集中在未修改区域；不在 P3 中扩大范围修复这些无关问题，也不把该历史结果当作最新全绿。
- P3 最终完整 `UV_NO_SYNC=1 make all PYTEST_JOBS=4`（含依赖修复后的代码）为 **5972 passed、45 skipped**，耗时 359.76 秒；Ruff、格式、mypy 与前端类型检查通过。日志为 `.artifacts/webskill-p3/make-all-p3-final.log`。非 editable 构建与真实验收不得并发重装同一 `.venv`。

## 3. 真实 UI 验收

执行 `scripts/webskill-p1/skill_draft_acceptance.py`，使用实际 Octop HTTP 服务、真实 Chromium、Chatbot 确认卡、Console 治理页与 OPFS：

1. 在真实 `RecordingStore` 写入唯一标记的 stopped `StepsDocument` fixture。本项验证转换边界，不冒充重新完成了一次 recorder daemon 录制。
2. 在增强浏览器页面点击“录制转技能”，通过父子消息调用受限 HTTP 接口。
3. 检查完整 SKILL.md 预览并明确确认。
4. 在 Console 中定位候选并看到唯一标记。
5. 直接读取 OPFS 中最终候选，确认 `draft` 状态、`generated` 来源、确认时间、SKILL.md 内容；不含 recordingId、sourceDigest 或 steps。
6. 监听 `/chat/completions` 请求，实际为 **0**；页面脚本错误为 **0**。

证据：`.artifacts/webskill-p1/ui/ui-result-skill-draft.json`，`status=passed`、`draftDigestRecomputed=true`、`candidatePersisted=true`、`consolePreviewed=true`。

验收中发现并修复了实际接线缺口：`createFsConsoleBackend` 不自动提供治理门面，必须为 Console 显式接入 `createGovernanceFacade`。非 editable 环境下还必须在最终前端构建后重装 Octop 包，避免测试服务读取 `.venv` 中的旧静态文件。

## 4. 尚未开放或待收尾

- P2 普通受信 TLS 的最新复验仍受本地模型无响应阻塞；`ui-result-trusted.json` 是失败证据，不是通过证据。
- wheel 的本机重建、哈希校验、独立安装与 fork 全量测试通过；但新 Docker 构建在 `node:20-slim` 下载阶段超过 20 分钟，未进入 wheel 安装步骤，不据此声称容器复验通过。`.dockerignore` 已排除 `.artifacts`，避免 socket 和验收材料进入后续构建上下文。
- P3 回放、技能执行、隔离 viewer、XLSX 和大屏的实施及已有证据见下文；录制生命周期、DOCX/PPTX/PDF 的最终结果按各自 JSON 记录，不用早期未实施描述替代最新状态。
- 模型会话生成技能已接入同一 sink，但本轮没有用真实模型重新验收该路径。
- 当前只证明本地 candidate，不表示生产支持或上游正式发布。


## 5. 受限录制回放（2026-11-19）

- 新增 `POST /api/browser-agent/sessions/{sessionId}/replays` 与 `DELETE /api/browser-agent/replays/{replayId}`。授权绑定 recording、StepsDocument 摘要、会话、起始 target、origin 白名单、300 秒有效期和动作上限；完成、取消、会话关闭或过期后不恢复旧回放。
- 主进程使用真实 `ReplayRunner` 和注入执行器。DOM 读取通过 PageAgent 重新感知并生成新 ref；点击/填写/选择使用现有单动作 grant，导航/新标签/按键等服务端直接动作使用独立一次性 replay grant。不开放 `eval_js`。
- 普通页面动作与直接动作共用动作配额。审计记录 replayId、recordingId、actionIndex、document/grant 摘要和结果，不记录输入值、完整 URL、原始 grant 或服务器路径。
- 多标签 target 仍受工作集上限约束。`set_checked`、`scroll_to` 及其他需要任意 JS 的旧回放路径在本切片明确失败，不冒充支持。
- 新接口只返回 replayId、recordingId、状态和步骤摘要；不返回 inputs、reportPath、failureArtifacts 或完整错误正文。客户端按独立 schema 校验，不把候选错误码复用为回放错误。

### 行为与真实验收

- `tests/unit/api/test_webskill_replay.py`：**10 passed**，包括真实 `ReplayRunner` 单次点击、grant 一次性/过期/跨会话、权限/Origin、取消时在途动作最多完成一次且不继续后续动作、审计脱敏和完成后失效。
- 前端回放/候选相关新增用例合计 **13 passed**，包括一次范围确认、取消不调用宿主、结果 schema、最近录制解析和父子窗口 session 边界。
- 真实 `replay_acceptance.py` 通过：在本地计数页面执行“打开页面 → 点击一次”，`confirmedRealClickCount=1`、`modelCalls=0`、`pageErrors=[]`。证据为 `.artifacts/webskill-p1/ui/ui-result-replay.json`。
- 验收曾实际命中“用户接管时不回放”，证明该路径 fail-closed；随后按真实 handoff 状态交还控制权后通过，未绕过接管限制。

## 6. 已发布技能执行（2026-11-19）

- 增强宿主真实完成“录制转候选 → Console 发布 → 受管目录安装 → 模型读取技能 → PageAgent 授权 → 页面点击”。
- 模型实际调用了 `read_skill_file`、`perceive_page` 和 `act_on_page`；技能正文中的唯一安全文本进入模型请求，未只凭技能名或模型自述判定。
- `ui-result-skill-execution.json` 为 `status=passed`、`skillPublished=true`、`skillContentObserved=true`、`grantRequests=1`、`replayRequests=0`、`confirmedRealClickCount=1`、`pageErrors=[]`。真实模型请求 3 次，无 Octop 模型或账本复用。
- 扩展宿主使用同一固定模板产物与现有 Playwright MV3 扩展环境执行验收；临时 spec 只用于本次验证，结束后删除，不形成 SDK 发布变更。

## 7. 内嵌隔离 viewer（2026-11-19）

- 宿主使用真实 `contentWindow` 完成握手；不再使用无法通过 `event.source === target` 的自定义窗口包装对象。真实窗口的 `close` 委托删除宿主 iframe，页面退出释放消息监听与 viewer 资源。
- 技能脚本经静态 `/webskill/sandbox/` 在 opaque origin 内运行。沙箱 CSP 允许受信宿主脚本、Worker blob 和技能模块的 `data:` 加载，不允许 inline/eval 或任意网络访问；普通脚本资源通过 `crossorigin="anonymous"` 携带 `Origin: null`，静态资源仅向该来源授予 CORS/CORP。
- `/webskill/viewer/` 的 CSP 从 SDK `viewerCspHeader` 生成，不允许 `allow-same-origin`。真实 Chromium 验证 `globalThis.origin === "null"`；`location.origin` 仍显示 URL 来源，不能单独用它判断文档是否 opaque。
- 实际执行 `authored-screen` 的 `publish.js`，写入 HTML/CSS 后经用户确认打开内嵌文档；样式生效，viewer 自身、外层 Dashboard 的非 parent 消息和父窗口错误 channel 均不能覆盖正文，关闭后宿主 DOM 被删除。
- 证据：`.artifacts/webskill-p1/ui/ui-result-viewer.json`，`status=passed`、`opaqueOrigin=true`、`storageBlocked=true`、`parentAccessBlocked=true`、`wrongSourceRejected=true`、`wrongChannelRejected=true`、`closeRemovedDom=true`、`pageErrors=[]`、`consoleErrors=[]`。此链路使用脚本化模型驱动真实 AgentLoop，不冒充外部模型验收。

## 8. 录制生命周期与本地依赖（2026-11-19）

- 本地 fork 的 `RecordingStore` 实现默认 30 天、每 profile 50 份（含进行中录制）的保留策略，进行中的录制必须先停止才允许删除。容量和过期清理由存储层统一执行，宿主只负责归属、权限与审计。
- `DELETE /api/browser-agent/recordings/{recordingId}` 校验登录、browser 权限、Origin、Fetch Metadata 与 profile 归属。删除前取消该录制的活动回放与未用 grant；删除标记阻止同一进程内旧步骤重新建立回放授权。
- fork 存储测试 7 项通过；完整 `UV_NO_SYNC=1 make all` 为 **267 passed、9 skipped**，Ruff、mypy 与提交钩子均通过。源码提交为 `641f57dcbdf1537dffa820741ecae4b9dc9d9ec8`，锁文件后续提交为 `43363a422894b0b2647b55e2a898e5aeb71616de`，均只在本地。
- Octop `pyproject.toml` 与 `uv.lock` 固定到 `1.1.0+webskill.local.2` 的本地 wheel，SHA-256 为 `b66182b2970113c5b39c1c2bf8a60656cee6b7bbc4b42eb85bbbca2f2b5ba5f7`。该 wheel 已用于非 editable 宿主回归；不代表公开发布或 Docker 构建通过。
- 真实 UI 删除验收通过：原始 steps/events/输入、截图、回放报告和录制目录均删除；删除后候选转换与回放不可用；tombstone 不含原始输入；OPFS 中独立受管技能原文保持不变；模型调用为 0。证据为 `.artifacts/webskill-p1/ui/ui-result-recording-lifecycle.json`，`status=passed`、`pageErrors=[]`。
- 前序失败曾定位到“只重建嵌套包而主 Dashboard 仍过滤新消息”及测试服务残留。最终验收使用 `make build-frontend` 顺序重建两者、非 editable 重装、独立端口与独立录制目录，不能复用旧失败结果或残留进程冒充新验收。

## 9. 文档产物与打印（2026-11-19）

| 场景 | 真实结果 | 证据 |
| --- | --- | --- |
| DOCX + 普通文档 PDF | 实际执行 authored-bulletin，下载 DOCX 后用 python-docx 重新打开并验证标题；真实 Chromium 打印 PDF 后用 pypdf 验证文本 | `ui-result-document-bulletin.json`、`bulletin.pdf` |
| PPTX + 三页打印版式 | 实际执行 authored-slides，下载 PPTX 后用 python-pptx 验证标题；Reveal 生成 3 个 `.pdf-page` | `ui-result-document-slides.json` |
| XLSX | 实际调用 export_spreadsheet，从 OPFS 取出真实工作簿，openpyxl 重新打开并验证表头、标记和值 | `ui-result-document-spreadsheet.json` |
| 中文大屏 | authored-screen 写入真实 HTML/CSS，Metric 组件挂载，中文与 CSS 尺寸生效；viewer 隔离、消息校验与关闭均通过 | `ui-result-viewer.json` |

- 上述 JSON 均位于 `.artifacts/webskill-p1/ui/`。文档场景使用脚本化模型驱动真实工具链；没有把脚本化回复写成真实外部模型能力。
- 文件“可打开”由对应 Python 解析器实际读取验证，不等于完成了 Word、PowerPoint 或 Excel 的 GUI 人工验收。
- 幻灯片验证覆盖 PPTX 和三页打印 DOM；**未单独宣称幻灯片原生 PDF 已通过**。测试曾尝试弹出独立打印页，但因固定 sandbox token 不允许 `allow-popups` 已撤回，没有为打印扩大 CSP。
- viewer 图表、结构化模型导出与 Reveal 复用 WebSkill 既有扩展示例的宿主模块，固定 `docx@9.7.1`、`pptxgenjs@4.0.1`、`reveal.js@6.0.1`；WebSkill SDK、Chatbot、Console 源码和版本均未改变。
- 只读 `npm audit --omit=dev` 发现 pptxgenjs 间接依赖 image-size 的 DoS 风险后，固定 `image-size@2.1.0` 并再次通过真实 PPTX/PPT 打印验收。复核日志 `.artifacts/webskill-p3/npm-audit-after-image-size.log` 中已无这两项 advisories；其余 15 项既有依赖告警未被强制升级或隐藏，当前不作为生产安全准入。

## 10. P3 回放后端评审整改（2026-11-19）

- page action 不再绕过录制 scope：当前 document 必须属于授权 origin、run.model_targets 与当前 modelTarget。DOM 读取、点击/填写/选择都经过该检查；direct 导航校验目标 origin，而非把起始页 origin 误当成目标 origin。
- page action 使用 `ReplayRegistry.issue_grant` 生成带 replayId/actionIndex/document/target/actionDigest 的一次性授权；PageAgent 通过受限消费回调使用它。取消、接管、过期、会话失效会拒绝后续动作，不再依赖未绑定的普通 PageAgent grant。
- direct navigate/press 复用 P2 `attach_model_target` 和 octop-browser 动作实现，写往 scoped modelTarget。switch_tab 仅改变模型逻辑指针，不调用会改变用户视图的 `browser.switch_tab`。非 base target 的旧 wait 明确失败，不读取 base 页面代替。
- 多 target 录制允许受控 switch_tab；新标签成功创建并纳入工作集后才能继续。真实 `replay_target_acceptance.py` 完成第二标签导航与一次点击，初始 `/first` 保留，`modelCalls=0`、`pageErrors=[]`。证据为 `.artifacts/webskill-p1/ui/ui-result-replay-target.json`。
- 每步都检查原始录制仍属于当前 profile 且实际存在；显式删除、自动保留清理及跨进程删除均 fail-closed。路由向执行器传入同一个 RecordingStore，避免读取/授权与删除检查使用不同来源。
- 联合回归 `test_webskill_replay.py`、`test_webskill_page_agent.py`、`test_webskill_skill_drafts.py` 为 **46 passed**，相关 Ruff 和 mypy 通过。
- `local-browser-fork.json` 固定 fork `43363a422894b0b2647b55e2a898e5aeb71616de` 和 wheel SHA-256 `b66182b2970113c5b39c1c2bf8a60656cee6b7bbc4b42eb85bbbca2f2b5ba5f7`。`build_local_browser.py` 已实际重建并验证哈希；wheelhouse 使用 allowlist 保留该 wheel 与 `.gitignore`，不再全部忽略。
- 本整改仍是本地 candidate；普通受信 TLS、Docker 新构建、生产安全准入与外部 Office GUI 人工验收均不因本整改自动通过。
- 整改后最终 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5976 passed、45 skipped**，耗时 340.61 秒；日志为 `.artifacts/webskill-p3/make-all-review-fix.log`。该数字对应新增回归后的代码，不与此前 5972 的历史结果混用。
- 将完整暂存变更应用到干净 detached worktree 后，`uv sync --frozen --extra local-embedding` 成功安装提交内 wheel；完整非 live 测试再次为 **5976 passed、45 skipped**，耗时 366.57 秒。日志为 `.artifacts/webskill-p3/clean-install.log` 与 `.artifacts/webskill-p3/clean-full-test.log`。`git ls-files wheelhouse` 现在包含 `.gitignore` 和实际 `.2` wheel。
