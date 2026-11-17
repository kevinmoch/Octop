# P2 实施与真实验证记录

> 状态：🟡 实施中；当前仅完成 P2-0 会话恢复竞态修复，仍为 candidate，不表示 P2 全部能力或生产支持已开放。

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
- target 选择同时要求 `parentId` 与当前页面一致，并保留 query，避免跨 tab 同 URL 或同 path 不同 query 误选。close 会移除 execution-context listener，detach 失败不会覆盖主结果；back 使用 scoped history，而非主页面 history。
- OOPIF 不调用不存在的 `Input.enable`；执行前重新建立 `DOM.getDocument` 映射，再解析 backendNodeId，不使用主文档或坐标兜底。
- 43 项后端专项测试通过，覆盖 scoped 读取/单击、默认上下文、attach/detach、父页面/query、listener 清理和导航后 grant 拒绝。真实跨站验收改为绝对 `localhost` 地址导航，保持 OOPIF 身份，最新证据为 `.artifacts/webskill-p1/3022565d08/result.json`。
- 评审整改后的全量 `make all PYTEST_JOBS=4` 为 `5916 passed, 45 skipped`；Ruff、1312 个文件的格式检查和 mypy（684 个源码文件）均通过。
- **合入阻断**：`e34bbdd` 仍是本地 editable dependency，未发布、未写入 Octop pin/lock；干净 CI/部署不能据此获得 `with_session`/`session_id`。因此 `oopif` capability 保持 `false`，本分支只做本地提交，不合入 `webskill-integration`，不声称 CI 或生产可用。

## 4. 已通过的验证

- `useWebSkillAgentSession.test.ts` 共 8 项：404/410 换建、非失效错误不换建、普通并发创建去重、并发 404 换建去重、迟到旧 404 忽略，以及关闭/卸载时迟到 create 的删除。
- `WebSkillFrame.test.tsx` 1 项组件/hook 集成测试覆盖旧 iframe 迟到 404；`transport.test.ts` 1 项覆盖 SDK frame 下钻与聚合；`model.test.ts` 3 项模型边界测试继续通过，前端合计 13 项。TypeScript、ESLint 和 Prettier 检查通过。
- 真实 UI 重新执行一次 ticket 404 注入，确认换建后恢复推流，模型回复、Console trace、主题切换和一次真实单击均通过；`pageErrors` 为空。证据为 `.artifacts/webskill-p1/ui/ui-result.json`，由同一套真实 UI 验收脚本生成。
- P2-0 首项及生命周期补强后的全量 `make all PYTEST_JOBS=4` 均为 `5907 passed, 45 skipped`；Ruff、1310 个文件的格式检查和 mypy（682 个源码文件）均通过。生命周期真实 UI 在全量结束后复验通过，首次模型超时未产生页面脚本错误。

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
make build-frontend
PATH="/Users/mochunhui/Git/octop-browser/.tools/uv/bin:$PATH" make all PYTEST_JOBS=4
```

真实 UI 先使用 `scripts/webskill-p1/ui_server.py` 启动隔离服务，再执行 `scripts/webskill-p1/ui_acceptance.py`。

## 5. 尚未开放

- 独立 target 工作集、双指针完整语义、共享写入者与跨进程 lease、容量和部署矩阵验收。
- `fill/select/scroll/back` 的真实副作用验收、其余动作审计增强、旧 JWT 截止门禁、受信代理 Origin 和占位 key 日志面验证。
- P3 的录制回放、技能执行和文档产物。
- LNA 真实用户拒绝与受信 TLS 环境仍未完成复验；本项不将这些边界标记为通过。
