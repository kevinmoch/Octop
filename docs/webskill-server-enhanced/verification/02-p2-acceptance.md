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

## 2. 已通过的验证

- `useWebSkillAgentSession.test.ts` 共 8 项：404/410 换建、非失效错误不换建、普通并发创建去重、并发 404 换建去重、迟到旧 404 忽略，以及关闭/卸载时迟到 create 的删除。
- `WebSkillFrame.test.tsx` 1 项组件/hook 集成测试覆盖旧 iframe 迟到 404；`model.test.ts` 3 项模型边界测试继续通过，前端合计 12 项。TypeScript、ESLint 和 Prettier 检查通过。
- 真实 UI 重新执行一次 ticket 404 注入，确认换建后恢复推流，模型回复、Console trace、主题切换和一次真实单击均通过；`pageErrors` 为空。证据为 `.artifacts/webskill-p1/ui/ui-result.json`，由同一套真实 UI 验收脚本生成。
- P2-0 首项及生命周期补强后的全量 `make all PYTEST_JOBS=4` 均为 `5907 passed, 45 skipped`；Ruff、1310 个文件的格式检查和 mypy（682 个源码文件）均通过。生命周期真实 UI 在全量结束后复验通过，首次模型超时未产生页面脚本错误。

实际执行命令：

```bash
cd /Users/mochunhui/Git/Octop/dashboard
npx vitest run \
  src/components/WebSkillFrame.test.tsx \
  src/pages/Control/WebSkillBrowser/useWebSkillAgentSession.test.ts \
  src/pages/Control/WebSkillBrowser/model.test.ts
npx tsc -b

cd /Users/mochunhui/Git/Octop
make build-frontend
PATH="/Users/mochunhui/Git/octop-browser/.tools/uv/bin:$PATH" make all PYTEST_JOBS=4
```

真实 UI 先使用 `scripts/webskill-p1/ui_server.py` 启动隔离服务，再执行 `scripts/webskill-p1/ui_acceptance.py`。

## 3. 尚未开放

- frame/OOPIF、独立 target 工作集、双指针完整语义、共享写入者与跨进程 lease、容量和部署矩阵验收。
- `fill/select/scroll/back` 的真实副作用验收、其余动作审计增强、旧 JWT 截止门禁、受信代理 Origin 和占位 key 日志面验证。
- P3 的录制回放、技能执行和文档产物。
- LNA 真实用户拒绝与受信 TLS 环境仍未完成复验；本项不将这些边界标记为通过。
