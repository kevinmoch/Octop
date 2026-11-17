# P2 实施与真实验证记录

> 状态：🟡 实施中；P2-0～P2-4 已完成本地候选验证，但共享写入全链路、依赖发布、LNA、容量和部署验收仍未收口，不表示 P2 全部能力或生产支持已开放。

## 1. P2-0 会话恢复竞态修复（2026-11-17）

- 基线为已同步并验收的 `webskill-integration@268c387d`，功能分支为 `feature/webskill-p2-session-recovery`。
- `ensureSession` 接收实际过期的 session ID。当前缓存已是其他新会话时直接复用，不再因迟到的旧 404 清除新会话。
- 同一旧会话的并发换建复用现有 `creatingRef` Promise，不引入第二套锁；恢复完成后到达的旧 404 不会再创建第三个会话。
- iframe 的 `onSessionExpired` 携带发起请求时的 `data.sessionId`，宿主按该 ID 判断是否换建，不把新会话 ticket 返回给旧 iframe。

### P2-0-a/b 生命周期补强（2026-11-17）

- `WebSkillFrame.test.tsx` 使用真实 hook 与组件消息链模拟：旧 iframe 发出 ticket 请求，宿主已换建新会话后旧请求才返回 404；断言旧 ID 被传入回调，但不产生第三个会话。
- hook 增加 generation。关闭或卸载时在途 create 不得重新写入缓存；迟到创建的会话会尝试删除，删除失败仍由 idle TTL 兜底。旧 Promise 的 finally 不会误清新一代创建 Promise。
- 生命周期分支为 `feature/webskill-p2-lifecycle-cleanup`，P2-0 已先合入 `webskill-integration`。

## 2. P2-1 同源 frame（2026-11-17）

- 功能分支为 `feature/webskill-p2-same-origin-frames`。`DOM.getDocument` 使用 `pierce=true`，服务端生成受限 frame 路径，客户端复用 `createFrameRouter` 下钻和聚合，不在服务端另造聚合器。
- 主文档只保留一个 document key；子 frame ref 绑定 `frameId:loaderId`。签发和执行 grant 前都重新核对当前 frame/loader，子 frame 独立导航后旧 ref/grant 明确失效，不回退到主文档或坐标。
- 感知与执行继续使用私有 `RefCache`，不污染 Octop 共享 `BrowserSession`。跨进程 iframe 的 owner 探测失败不会阻断其他同源 frame；目标 frame 不存在或不可读时仍返回明确的 scope 错误。
- 评审整改后，选中 frame 在投影前会重新获取一次快照，避免旧 DOM 与新 loader 拼接；shadow host 被加入 frame path，避免不同 shadow root 下的 `nth-of-type` 冲突。
- 37 项初始后端专项和后续 43 项评审整改专项均通过；13 项前端测试通过，覆盖 SDK 路由下钻与聚合。
- 真实 Chromium 完成同源 iframe 一次点击、重放拒绝及独立导航后的 grant 拒绝，证据为 `.artifacts/webskill-p1/c1e139f083/result.json`。真实 UI 也通过 Chatbot 的 `OctopPageAgentTransport/createFrameRouter` 完成主页面与同源 iframe 各一次点击，`confirmedSameOriginFrameClickCount=1`。
- 同源 frame 全量 `make all PYTEST_JOBS=4` 为 `5910 passed, 45 skipped`；Ruff、1311 个文件的格式检查和 mypy（683 个源码文件）均通过，真实 UI 主链路同步通过。`frames` 标记为同源支持，`oopif` 仍为 false。

## 3. P2-2 OOPIF（2026-11-17，本地 candidate，暂不合入）

- `octop-browser-webskill` 的 `feature/session-scoped-cdp@e34bbdd` 为 `CDPClient.send` 增加向后兼容的 `session_id`，并提供 scoped client；245 项 library 测试通过、9 项跳过。Octop 功能分支为 `feature/webskill-p2-oopif`。
- 感知、签发 grant 和执行都通过 `Target.attachToTarget` 进入真实独立 iframe target，固定默认 execution context 和 `oopif:<targetId>:<loaderId>`。动作结束后 detach，导航、替换或上下文不匹配时 fail-closed。
- target 选择同时要求 `parentId`、iframe 的 `DOM.describeNode.frameId` 与 scoped 根 frame 一致，并核对当前 URL（含 query），避免跨 tab、同 path 不同 query 或 history 后陈旧 `src` 误选。close 会移除 execution-context listener，detach 失败不会覆盖主结果。back 只作用于当前 modelTarget；独立 target 使用 scoped history，`@document` 不表达 OOPIF frame 的独立 history。
- OOPIF 不调用不存在的 `Input.enable`；执行前重新建立 `DOM.getDocument` 映射，再解析 backendNodeId，不使用主文档或坐标兜底。
- 43 项后端专项测试通过，覆盖 scoped 读取/单击、默认上下文、attach/detach、父页面/query、listener 清理和导航后 grant 拒绝。真实跨站验收改为绝对 `localhost` 地址导航，保持 OOPIF 身份，最新证据为 `.artifacts/webskill-p1/fd849444f6/result.json`。
- 早期评审整改后的全量 `make all PYTEST_JOBS=4` 为 `5916 passed, 45 skipped`；Ruff、1312 个文件的格式检查和 mypy（684 个源码文件）均通过。最新全量结果见第 6 节。
- **合入阻断**：`e34bbdd`、`9b18040`、`927bfe3` 仍是本地 editable dependency，未发布、未写入 Octop pin/lock；干净 CI/部署不能据此获得 scoped client、command gate 或 ref scroll。因此 `oopif` capability 保持 `false`，本分支只做本地提交，不合入 `webskill-integration`，不声称 CI 或生产可用。

## 4. P2-3 target 工作集与双指针（2026-11-17，本地 candidate）

- `base_target_id` 保留原始 CDP 页面，`model_target` 独立维护。用户切换标签只改变 viewTarget，不再触发 modelTarget 强制跟随；目标被关闭时返回 `PAGE_TAB_EXPIRED`，不自动落到其他页面。
- 模型需要操作其他 target 时复用 scoped attach 生命周期，读取 `document.readyState` 后再绑定 loader，前后 identity 使用同一个 scoped client，避免新旧 target 混用。
- 动作后对比 page target 集合，恰有一个新 target 时才更新 modelTarget，并清空旧 document/ref/grant。多个或没有可判定的新 target 时不猜测。
- 45 项后端专项通过，覆盖用户切页、新窗口、目标关闭和加载中 target。真实 `target=_blank` 验收通过，证据为 `.artifacts/webskill-p1/40be551f42/result.json`。
- 全量 `make all PYTEST_JOBS=4` 为 `5918 passed, 45 skipped`；Ruff、1313 个文件的格式检查和 mypy（685 个源码文件）均通过。该能力同样受 scoped dependency 未发布约束，capability 不标记为生产可用。

## 5. P2-4 lease、真实动作与安全补强（2026-11-17，本地 candidate）

- `octop-browser-webskill` 本地提交 `9b18040` 为 scoped CDP 增加 command gate，并保证 target 切换保留 gate；`927bfe3` 让 scroll 按元素 ref 的中心发送 wheel，保留原 viewport 默认行为。该阶段 library 全量为 **248 passed, 9 skipped**；评审整改后的最新结果为 **249 passed, 9 skipped**。
- Octop 的 `profile_lease` 使用 OS 文件锁协调同一共享 profile，锁文件只含 pid/profile/acquiredAt，不含伪造的固定租约有效期；进程终止后由 OS 释放。`profile_command_gate` 接入 BrowserSession 初次创建、启动失败重试及真实验收直接创建路径。两个 spawn 进程写入区间不重叠，持锁进程被终止后另一进程可获取锁。锁等待超时返回 `WRITE_LOCKED`；Windows 加锁和解锁均固定到偏移 0 的同一字节，跨平台逻辑有单测，但未冒充真实 Windows CI 证据。
- 真实 Chromium 验收发现的主页滚动后 frame 不可见点击问题已通过 `DOM.scrollIntoViewIfNeeded` 修复。scroll 遵循 WebSkill 公共契约使用元素 ref，不私造 `@document` frame 语义；`back` 才使用 document ref。主文档和同源 frame 的 fill/select/scroll、主文档 back、OOPIF 的 fill/select/scroll 均有真实副作用断言；不宣称独立的 OOPIF frame history back。
- OOPIF target 发现使用 `DOM.describeNode.frameId`，在 scoped target 中核对根 frame、parent 和当前 URL（含 query），不读取 history 后已过期的 iframe `src`。真实诊断 `.artifacts/webskill-p1/7361592068/oopif-target-diagnostics.json` 证明主页面 frame tree 不包含 OOPIF，不能假定 targetId 等于 frameId。
- 动作审计在 execute 前快照 document，记录 document 摘要、documentTarget、ref 和 grantDigest，不记录填值、原始 grant/ticket 或 URL query；grant 与 action 可通过同一 grantDigest 对账。
- 旧工作台 JWT 兼容入口仍保留到需求约定的 1.1.0，代码在版本达到 1.1.0 且未删除该入口时拒绝加载；真实增强链路的 WS query 不含 token。Origin 复用 Uvicorn 的 trusted proxy 处理结果，单测验证回环受信代理可采用 HTTPS scheme、非受信来源的伪造 proto 被拒绝，未放开 `forwarded_allow_ips=*`。
- `model-auth-probe.mjs` 使用真实本地 HTTP 鉴权端点验证占位兼容请求只发送一次、返回 401、不报 mock 成功且不写入配置；输出为 `endpointRequests=1, httpStatus=401, placeholderPersisted=false`。
- 71 项浏览器后端专项通过。最新真实验收为 `.artifacts/webskill-p1/fd849444f6/result.json`，覆盖主文档/frame/OOPIF 动作、target=_blank、接管、grant 防重放、权限隔离和审计。

### P2-4 评审整改（2026-11-17）

- `389b775` 将 launch/attach 与初始 WebSocket connect 纳入外层 command gate，并在初始化命令前释放，避免不可重入锁嵌套；library 全量为 **249 passed, 9 skipped**。Octop 新增两个 spawn 进程的真实 `BrowserSession.create` 测试，以计时驱动桩验证 launch/connect 不重叠。
- back 在 modelTarget 为独立 target 时只调用 scoped history，不再调用 base `browser.go_back()`；真实 `target=_blank → 子页前进 → back` 返回子页且 base 保持 `/next`。原 `fd849444f6` 的 OOPIF back 语义已撤销，`@document` 不表示 OOPIF frame 独立历史。
- 新 target 的 before/after 集合固定从根页面 client 查询，不依赖 OOPIF scoped client。scoped 元素执行前重新 `DOM.getDocument`，避免重新 attach 后 `DOM.requestNode` 返回 0。`WRITE_LOCKED` 不再被转换为 `PAGE_TAB_EXPIRED`。
- trusted proxy 测试同时覆盖 HTTP 和 WebSocket/wss：受信回环代理允许，非受信来源的转发 proto 被拒绝。OOPIF 清理对 listener 和 detach 异常均不覆盖主结果。
- 主 Dashboard 构建会清空嵌套 WebSkill 目录；提交钩子已改为在主构建后执行 `npm --prefix webskill run build`，最终入口实际存在。真实 UI 通过主页面、同源 frame、OOPIF 各一次点击，`confirmedRealClickCount=3`、`confirmedOopifClickCount=1`、`pageErrors=[]`。
- 整改后明确选择的专项命令为 **77 passed**，证据为 `.artifacts/webskill-p1/ae93583fd0/result.json` 与 `.artifacts/webskill-p1/ui/ui-result.json`。
- 整改代码的最终全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5933 passed, 45 skipped**，Ruff、格式和 mypy（686 个源码文件）均通过。该结果不替代依赖发布、共享写入全链路或部署准入。

## 6. 已通过的验证

- `useWebSkillAgentSession.test.ts` 共 8 项：404/410 换建、非失效错误不换建、普通并发创建去重、并发 404 换建去重、迟到旧 404 忽略，以及关闭/卸载时迟到 create 的删除。
- `WebSkillFrame.test.tsx` 1 项组件/hook 集成测试覆盖旧 iframe 迟到 404；`transport.test.ts` 1 项覆盖 SDK frame 下钻与聚合；`model.test.ts` 3 项模型边界测试继续通过，前端合计 13 项。TypeScript、ESLint 和 Prettier 检查通过。
- 真实 UI 重新执行一次 ticket 404 注入，确认换建后恢复推流，模型回复、Console trace 和主题切换均通过；主页面、同源 frame、OOPIF 各一次真实单击，`pageErrors` 为空。最新证据为 `.artifacts/webskill-p1/ui/ui-result.json`，`confirmedRealClickCount=3`、`confirmedSameOriginFrameClickCount=1`、`confirmedOopifClickCount=1`，WS query 仅有 width/height/ticket。
- P2-0 首项及生命周期补强后的全量 `make all PYTEST_JOBS=4` 均为 `5907 passed, 45 skipped`；Ruff、1310 个文件的格式检查和 mypy（682 个源码文件）均通过。生命周期真实 UI 在全量结束后复验通过，首次模型超时未产生页面脚本错误。
- P2-4 本轮全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5925 passed, 45 skipped**；Ruff、1315 个文件的格式检查和 mypy（686 个源码文件）均通过。其后新增 lease timeout/Windows 偏移测试纳入上述 71 项专项，并由最终提交钩子再次覆盖。

实际执行命令：

```bash
cd /Users/mochunhui/Git/Octop/dashboard
npx vitest run \
  src/components/WebSkillFrame.test.tsx \
  src/pages/Control/WebSkillBrowser/useWebSkillAgentSession.test.ts \
  src/pages/Control/WebSkillBrowser/transport.test.ts \
  src/pages/Control/WebSkillBrowser/model.test.ts
npx tsc -b

cd /Users/mochunhui/Git/Octop
UV_NO_SYNC=1 PATH="/Users/mochunhui/Git/octop-browser/.tools/uv/bin:$PATH" uv run --no-sync pytest \
  tests/unit/api/test_webskill_sessions.py \
  tests/unit/api/test_webskill_page_agent.py \
  tests/unit/api/test_webskill_http.py \
  tests/unit/api/test_webskill_lease.py \
  tests/unit/api/test_browser_stream_mouse.py \
  tests/unit/api/test_browser_stream_keyboard.py -q
# 77 passed

make build-frontend
PATH="/Users/mochunhui/Git/octop-browser/.tools/uv/bin:$PATH" make all PYTEST_JOBS=4
```

真实 UI 先使用 `scripts/webskill-p1/ui_server.py` 启动隔离服务，再执行 `scripts/webskill-p1/ui_acceptance.py`。

## 7. 尚未开放

- record daemon、ReplayRunner、Dashboard 原始输入和 harness 的统一写入协调与跨进程接管尚未完成；已有 lease/gate 只覆盖明确注入的客户端，不能据此通过 AC-15、AC-40、AC-51、AC-60。
- 工作集上限、后台 viewTarget 连续出帧、单采集多订阅、容量/p95 和 Kubernetes 亲和/持久化矩阵未完成；多副本与 `emptyDir` 继续 blocked。
- OOPIF 内再嵌套 frame 的下钻与聚合仍未开放；当前真实 UI 证据只覆盖一层 OOPIF。
- `e34bbdd`、`9b18040`、`927bfe3`、`389b775` 未发布、未锁定到 Octop 依赖；干净 CI、部署和生产支持不能依赖本机 editable 环境。
- P3 的录制回放、技能执行和文档产物。
- LNA 真实用户拒绝与受信 TLS 环境仍未完成复验；本项不将这些边界标记为通过。
