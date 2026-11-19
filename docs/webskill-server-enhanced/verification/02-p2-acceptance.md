# P2 实施与真实验证记录

> 状态：🟡 实施中；P2-0～P2-7 已有本地候选证据，但依赖发布、受信 TLS 和部署验收仍未收口，不表示 P2 全部能力或生产支持已开放。

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

### P2-5 写入协调与资源收尾（2026-11-17，本地 candidate）

- `862e977` 将唯一 OS lease 实现下沉到 octop-browser，Octop 保留异常翻译且锁路径不变。library 全量为 **257 passed, 9 skipped**。真实 record daemon 在另一进程持锁时等待，释放后正常启动、停止并关闭。
- daemon 主客户端和后续发现 target 的客户端均使用同一 profile gate。browser_tool 支持宿主配置 gate 工厂，首次创建和断线重建都覆盖；已存在但未协调的会话明确失败。BrowserProfileMiddleware 在接管时阻止 harness 写动作，包括 `eval_js`，不再只改变 UI 状态。
- ReplayRunner 支持可选执行器；Octop 注入已验证带 gate 的 BrowserSession，移除路由用 profile 参数，缺 gate 时明确 503，不回退默认 browser_tool。回放路由要求 `browser` 权限。该接口工作不代表 P3 的逐步 grant、回放状态机或技能执行已开放。
- target 工作集记录当前任务的已授权页面，断线重用时清空。触顶时不移动 modelTarget、不扩大权限，同时保留真实动作成功及诊断原因，不伪称已回滚新页面。
- 同一 profile 的观看端共享 `ProfileFrameHub`，限制最多 4 个订阅，最后一个订阅关闭后取消采集并清除缓存。单测验证共享帧、上限与回收；真实双 WS 验收确认两个观看端均有帧且只有一个采集 hub。
- 全量回归定位到旧 `test_browser_stream_frames` 将共享的 `asyncio.sleep` 替换为不让出事件循环的假函数，导致新抓图任务忙循环。测试已改为真实调度，按实际发送帧数停止，并允许采集端最多一个在途尾部帧；抓图与 hub 专项共 3 项通过。
- 最新真实证据为 `.artifacts/webskill-p1/0b75b34fe0/result.json`。真实 UI 三击链路已在本次 writer/hub 改动后重新执行并通过，证据为 `.artifacts/webskill-p1/ui/ui-result.json`。
- **首次 LNA 验收未通过**：在隔离局域网 HTTPS Origin + TLS 模型桩下，权限拒绝和 Permissions-Policy 拒绝均仍发送了 1 次真实 POST。该失败触发了下述应用层失败关闭整改，不作为环境噪声忽略。

### P2-5 评审整改（2026-11-17，本地 candidate）

- `c1a8202` 让同一异步上下文中的同一路径 lease 可重入，`switch_to_target` 的 activate、HTTP target 查询、旧 client 关闭和新 client 连接处于同一外层事务；`c359041` 补充独立 Context 不重入的隔离验证。library 全量为 **261 passed, 9 skipped**。
- FrameHub 捕获异常后保留任务并继续后续采集，已有订阅可恢复；首订阅失败会清理空 hub。抓图不再固定首个观看端的 session，而是每次按 profile 解析当前 session。FrameHub/stream 专项为 **6 passed**。
- target 工作集溢出时保留旧 document、refs 和 grant 上下文，只追加诊断原因；`WRITE_LOCKED` 通过公共 WS 错误映射保持为 `WRITE_LOCKED`，不再降级为通用工具失败。
- 接管写拦截覆盖 browser_use、已配置宿主 gate 的直接 browser_tool、Replay 的每个动作及录制启动；录制停止仍允许用于资源清理。独立 daemon 进程仍没有跨进程 owner 状态，本项不宣称跨进程接管已完成。
- LNA 在创建模型客户端前按源/目标地址空间查询 `local-network` 或 `loopback-network`，`denied` 时以既有 `LLM_REQUEST_FAILED` 失败关闭且不调用 fetch；不查询旧 `local-network-access`。权限拒绝与 Permissions-Policy 拒绝的真实 Chromium 复验均为 `modelRequests=0`，证据分别为 `.artifacts/webskill-p1/ui/ui-result-lna-permission.json` 和 `ui-result-lna-policy.json`。
- 整改后的真实 Chromium/HTTP/WS/daemon 证据为 `.artifacts/webskill-p1/9c8c7c30bb/result.json`；普通真实 UI 三击链路也已重新执行通过。上述 LNA 证据使用隔离自签 TLS，不替代生产受信 TLS 验收。

### P2-6 跨进程接管、动作事务与持续推流（2026-11-17，本地 candidate）

- `1504919` 增加按 profile 的原子 owner 状态文件，内容与锁元数据分离；record daemon 启动前及后续命令 gate 均读取状态，用户接管时返回/抛出 `WRITE_BLOCKED`。停止录制和 `close_session` 作为清理操作放行。
- PageAgent 动作和 Dashboard 人工输入同时使用进程内锁与 OS lease，browser_tool 的单次调用（含重连后的重试）也使用外层事务。新增测试证明等待 lease 的模型动作在接管后不产生点击；已执行动作不回滚。
- 真实独立 daemon 在人工接管后拒绝录制启动。完整证据为 `.artifacts/webskill-p1/4bcc25d69b/result.json`，覆盖 AC-14/15/40/51/60 的本地候选路径；依赖发布与目标部署复验仍是生产前置。
- AC-45 使用集成 WS/FrameHub 连续执行完整 60 秒，4 个观看端各收到 404 个真实帧，viewTarget 保持 `hidden`。最大帧间隔约 0.162 秒，p95 为 0.153～0.161 秒，均低于 P0 的 1.5 秒上限；额外观看端关闭后订阅数恢复为 2，全部关闭后 hub 回收。证据为 `.artifacts/webskill-p1/4bcc25d69b/stream.json`，不含图像、token 或输入值。
- 本批专项为 **106 passed**，library 全量为 **265 passed, 9 skipped**。该结果覆盖 AC-32/34/45 的本地四观看端场景，不代表多副本或 Kubernetes 容量结论。
- P2-6 最终全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5947 passed, 45 skipped**（316.17 秒）；Ruff、1319 个文件的格式检查和 mypy（688 个源码文件）均通过。

### P2-7 嵌套 OOPIF（2026-11-18，本地 candidate）

- 新增 `FrameAttachment` 复合 frame 身份，使用 `>` 分隔多层 target/frame；直接 OOPIF key 保持兼容。读取、grant 签发与执行均逐层 attach 并校验 loader，嵌套 key 不回退主文档或坐标。
- execution context 按 `Page.getFrameTree` 根 frameId 选择，下一层 scoped client 回退到根 CDP 连接创建，避免多层上下文中选错 URL 或禁止二次 scope。ref 只在当前完整 frame key 范围内更新，不再清空其他有效嵌套引用。
- 真实 Chromium 同时通过 OOPIF→同源 frame 和 OOPIF→OOPIF 的读取与点击，并在嵌套同源 frame 导航后拒绝旧 grant。证据为 `.artifacts/webskill-p1/2bca0ac760/result.json`，同一次执行也再次通过原有 60 秒后台推流与跨进程接管验收。
- 本项不代表嵌套回放状态机已开放；P3 的逐步 grant 与回放授权仍按后续阶段实施。
- P2-7 全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5947 passed, 45 skipped**（350.50 秒）；Ruff、1319 个文件的格式检查和 mypy（688 个源码文件）均通过。

### P2-8 部署、受信代理与持久化（2026-11-18，本地 candidate）

- Helm 默认保持每 Pod `emptyDir`；新增单副本 `persistence.enabled`、独立 PVC 和 `helm.sh/resource-policy: keep`，多副本共用本地持久目录在模板渲染时拒绝。`config.forwardedAllowIps` 仅接受显式代理 CIDR，模板拒绝 `*`。
- kind `v1.37.0`、Ingress NGINX `1.15.1`、真实 Octop/Chromium/PostgreSQL 已部署。真实验收通过一次性 ticket 启动服务器 Chromium，再用受限 PageAgent 读取和点击 fixture，不直接绕过服务边界操作 CDP。
- 单副本 PVC 的真实证据为 `.artifacts/webskill-p1/k8s/persistent-result.json`：Pod UID 更换、旧 PageAgent 会话返回 404、删除前 Cookie 已落盘、替换后仍读取 `signed-in` 且可继续执行动作。该证据不表示旧内存会话被恢复，也不表示多副本共享锁。
- 实际部署发现老版本 Uvicorn 将代理 HTTPS 在 WS scope 中重写为 `wsss`，原有仅识别 `wss` 的检查会拒绝握手。`check_websocket_origin` 已显式归一化 `https/wss/wsss`，非安全 scheme 仍拒绝；`tests/unit/api/test_webskill_http.py` 合计 **20 passed**，并已在加载前直接断言镜像内包含修复。
- 双副本 `emptyDir` 的真实证据为 `.artifacts/webskill-p1/k8s/emptyDir-result.json`：同一 `OCTOP_AFFINITY` 下重复读取同一会话成功，删除所属 Pod 后旧会话 404，另一副本读取 `anonymous` 并可重新登录和操作。测试身份在故障切换前预置；本项不验证动态用户复制，也不宣称跨 Pod 文件锁。
- 历史普通 TLS 成功声明因 `ui-result-https.json` 被 LNA 覆盖而撤销；当前独立复验见 P2-10，旧文件不再充当该链路证据。
- 两种 LNA 拒绝复验均使用局域网 HTTPS Origin 和受信证书，未启用 `ignore_https_errors`：`ui-result-trusted-lna-permission.json` 与 `ui-result-trusted-lna-policy.json` 均为 `status=passed`、`lnaDenied=true`、`structuredErrorVisible=true`、`modelRequests=0`。

### P2-9 本地 fork 与依赖 pin（2026-11-18）

- 用户决定不再向远程推送或修改上游 PR。`TencentCloud/octop-browser#26` 已关闭，远程功能分支已删除；本地提交保留。
- 本地 fork 版本为 `1.1.0+webskill.local.1`，明确不是上游正式版本。`mcp` 依赖范围修正为 `>=1.9,<2`。
- 本地 fork 的真实浏览器验收已通过，证据为 `.artifacts/webskill-p1/aa2c71370a/result.json`：嵌套 frame、target、grant、接管、daemon 和审计均通过。四个观看端各收到 408 帧、`uniquePayloads=2`，p95 帧间隔低于 0.159 秒，证据为同目录 `stream.json`。
- 本节其余历史测试数字只对应历史工作树；最终数字以 P2-10 冻结记录为准。

### P2-10 最终评审整改（2026-11-18，本地代码冻结候选）

- fork 版本提交固定为 `42d6f7c2c59e6617a84658d615a08ce2bfb5f0cf`；`scripts/webskill-p1/build_local_browser.py` 要求 HEAD 与该提交一致、工作树干净，并使用提交时间重建 wheel。两次重建 SHA-256 均为 `d4861580231ecad869dba9ca22b17f719745d19d9e230dee8a6a28c339d12001`。
- Octop 改为引用 `wheelhouse/octop_browser-1.1.0+webskill.local.1-py3-none-any.whl`，`uv.lock` 已包含同一 SHA-256；`uv sync --frozen --no-install-project` 与独立 wheel 非 editable 安装通过，后者全量 **265 passed, 9 skipped**。Docker 构建显式复制 wheelhouse，不再依赖构建机上的目录源码。
- `control_owner_for` 每次读取共享 owner 文件，移除进程内旧状态；真实文件切换回归通过。旧 `_CONTROL_OWNERS` 测试均已改为实际文件状态。相关录制/接管/中间件专项 **20 passed**。
- LNA 主机分类补齐 `localhost.`、浏览器规范化 IPv4 简写、IPv4-mapped IPv6 与 CGNAT；模型专项 **7 passed**，TypeScript 通过。普通域名解析后的地址空间仍由浏览器在连接时校验，应用层不伪造 DNS 解析结论。
- UI 验收按普通 HTTPS、受信 TLS、LNA permission/policy 分文件保存，不再相互覆盖。
- 普通受信 TLS 的最新真实结果为 **未通过**：证书、登录、会话恢复与推流已进入，但模型回复等待 90 秒超时，`.artifacts/webskill-p1/ui/ui-result-trusted.json` 为 `failed`；直接最小模型请求也在 45 秒内无响应。未启用忽略证书错误，不能将本次结果记为通过。
- **最终评审允许启动 P3；普通受信 TLS 仍待模型恢复后复验。** P2 是本地代码候选，不宣称生产验收完成。
- 早期完整提交门禁为 **5953 passed, 2 failed, 83 skipped**；两个失败均为缺失 `local-embedding` 可选依赖后的知识库行为，不涉及浏览器/候选实现。证据保留为 `.artifacts/webskill-local/p2-optional-deps-failed.log`。通过 `uv sync --frozen --extra local-embedding` 恢复锁定依赖后，相关文件 **52 passed**；未修改或禁用既有测试。
- 依赖恢复后的最终 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5957 passed, 45 skipped**（384.54 秒），Ruff、格式和 mypy 均通过，日志为 `.artifacts/webskill-local/make-all-restored-final.log`。此前 Laya 健康检查单次时序失败已独立复跑通过，未修改其测试。

## 6. 已通过的验证

- P2-8 本轮 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5951 passed, 45 skipped**（427.87 秒）；Ruff、格式和 mypy 通过，日志为 `.artifacts/webskill-p1/make-all-p2-final.log`。该结果覆盖当前工作树，不替代正式发布、pin 和非 editable 验证。
- `useWebSkillAgentSession.test.ts` 共 8 项：404/410 换建、非失效错误不换建、普通并发创建去重、并发 404 换建去重、迟到旧 404 忽略，以及关闭/卸载时迟到 create 的删除。
- `WebSkillFrame.test.tsx` 1 项组件/hook 集成测试覆盖旧 iframe 迟到 404；`transport.test.ts` 2 项覆盖 SDK frame 下钻/聚合与 `WRITE_LOCKED` 透传；`model.test.ts` 4 项模型边界测试（含 LNA 拒绝时不调用 fetch）继续通过，前端合计 15 项。TypeScript、ESLint 和 Prettier 检查通过。
- 真实 UI 重新执行一次 ticket 404 注入，确认换建后恢复推流，模型回复、Console trace 和主题切换均通过；主页面、同源 frame、OOPIF 各一次真实单击，`pageErrors` 为空。最新证据为 `.artifacts/webskill-p1/ui/ui-result.json`，`confirmedRealClickCount=3`、`confirmedSameOriginFrameClickCount=1`、`confirmedOopifClickCount=1`，WS query 仅有 width/height/ticket。
- P2-0 首项及生命周期补强后的全量 `make all PYTEST_JOBS=4` 均为 `5907 passed, 45 skipped`；Ruff、1310 个文件的格式检查和 mypy（682 个源码文件）均通过。生命周期真实 UI 在全量结束后复验通过，首次模型超时未产生页面脚本错误。
- P2-4 本轮全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5925 passed, 45 skipped**；Ruff、1315 个文件的格式检查和 mypy（686 个源码文件）均通过。其后新增 lease timeout/Windows 偏移测试纳入上述 71 项专项，并由最终提交钩子再次覆盖。
- P2-5 初次收尾全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5939 passed, 45 skipped**（370.44 秒）；Ruff、1318 个文件的格式检查和 mypy（688 个源码文件）均通过。
- P2-5 评审整改后全量 `UV_NO_SYNC=1 make all PYTEST_JOBS=4` 为 **5945 passed, 45 skipped**（342.38 秒）；Ruff、1318 个文件的格式检查和 mypy（688 个源码文件）均通过。浏览器专项 **104 passed**、前端 **15 passed**、library **261 passed, 9 skipped**；真实 daemon/页面、普通 UI 与两种 LNA 拒绝场景均有独立证据。

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
  tests/unit/api/test_webskill_ownership.py \
  tests/unit/api/test_browser_stream_mouse.py \
  tests/unit/api/test_browser_stream_keyboard.py \
  tests/unit/api/test_browser_stream_frames.py \
  tests/unit/api/test_browser_frame_hub.py \
  tests/unit/agents/test_browser_profile_middleware.py \
  tests/integration/test_browser_record_replay_api.py -q
# 106 passed

make build-frontend
PATH="/Users/mochunhui/Git/octop-browser/.tools/uv/bin:$PATH" make all PYTEST_JOBS=4
```

真实 UI 先使用 `scripts/webskill-p1/ui_server.py` 启动隔离服务，再执行 `scripts/webskill-p1/ui_acceptance.py`。LNA 场景设置 `WEBSKILL_P1_LNA_DENY=1`，分别使用 `Browser.setPermission` 的 granular 权限拒绝或 `WEBSKILL_P1_LNA_POLICY=1`，并保留各自 artifact。完整 AC-45 执行包含在 `real_acceptance.py` 中，每次实际计时 60 秒。

## 7. 尚未开放

- 跨进程接管、动作事务及 AC-32/34/45 已有本地候选证据；锁等待、更完整运行指标和目标负载仍需在实际部署中复验，不能据此宣称 AC-14/15/40/51/60 已获生产准入。
- Kubernetes 单副本 PVC 与双副本 `emptyDir` 已有真实证据，但仅标记 `candidate`；正式发布/pin 前不扩大为生产支持。
- 本阶段已改用本地 fork `1.1.0+webskill.local.1` 与 `editable=false` 路径 pin，不再等待上游审批；上游 PR `#26` 已关闭，远程功能分支已删除。该本地版本仍需完成最终真实浏览器、全量和部署复验，不能当成上游正式发布或生产默认依赖。
- P3 的逐步授权回放、技能执行和文档产物。
- 受信 TLS 普通链路和两种 LNA 拒绝链路已完成隔离验收；真实用户授权/拒绝矩阵及其他浏览器和网络拓扑仍需按兼容矩阵逐项验证，不能将当前证据扩大为所有环境均已通过。
