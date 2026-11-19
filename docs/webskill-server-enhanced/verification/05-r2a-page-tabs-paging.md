# R2A 页面能力：tab workset 与感知分页（实现中）

> 状态：🟡 **代码与契约已落地，真实 UI 验收未完成**。不得把本文件写成 R2A 通过。  
> 日期：2026-11-19  
> 基线：Octop `64bef103` + R0/R1；SDK `0.29.0`。  
> 模型：R2A 的最终页面/分页验收仍需真实模型；本文件先记录已实现端口和后端契约。

## 1. 本次实现

### pageTabs

- 后端 `src/octop/infra/control/browser/webskill/tabs.py`：
  - 只暴露 `model_targets` 工作集，不枚举用户全部标签页；
  - target id 只留在宿主，模型只看到 `tab_` 开头的随机 opaque handle；
  - focus 只改 `session.model_target`，**不调用** `browser.switch_tab`；
  - close 禁止关闭 origin/base 页，关闭后移除句柄和 target；
  - reset 只丢工作集，不关闭任何页面；
  - 新页面可经 `tabs/admit` 接入工作集。
- HTTP API：
  - `GET /api/browser-agent/sessions/{id}/tabs`
  - `POST .../tabs/{handle}/focus`
  - `DELETE .../tabs/{handle}`
  - `POST .../tabs/admit`
  - `POST .../tabs/reset`
  - 所有变更校验 same-origin；跨用户通过 session ownership 拒绝。
- 宿主：
  - `dashboard/webskill/src/pageTabs.ts` 实现 `TabWorksetPort` 与 `ActionTabWatcher`；
  - `host.ts` 注入 `TabWorksetPolicy`、`pageTabs`、`pageActions.workset`；
  - `WebSkillFrame` 和 `browserAgentApi` 增加受控桥接。

### pagePerceptionPaging

- `host.ts` 把 `PERCEPTION_PAGING_DEFAULTS` 作为 `pagePerceptionPaging` 注入 `ChatbotHostAdapter`；
- 不私造分段协议，使用 SDK 的 `PerceptionPagingBudget`；
- 页面超过预算时由 SDK 返回 continuation，而不是宿主静默截断。

### target/frame 矩阵

- 现有 target/frame/OOPIF 真实证据保留；
- R2A 的真实 UI 回归仍待补：双 tab、focus 不改用户视图、close origin 拒绝、分页续读。

## 2. 已运行的测试

```bash
UV_NO_SYNC=1 ... pytest tests/unit/api/test_webskill_tabs.py \
  tests/unit/api/test_webskill_tabs_api.py -q
# 3 passed

npm --prefix dashboard test -- --run \
  src/pages/Control/WebSkillBrowser/pageTabs.test.ts \
  src/components/WebSkillFrame.test.tsx
# 6 passed
```

后端测试验证：

- handle 不含 target id；
- focus 后 `model_target` 改变，`browser.switch_tab` 调用为 0；
- origin 页不可关闭；
- close 调 `browser.close_tab`，不切换用户视图；
- reset 清空句柄但不关闭页面。

前端测试验证：

- 端口只显示 handle/title/url；
- focus/close 走 host bridge，不接触 CDP；
- watcher 只观察已授权工作集，并遵守 timeout。

## 3. 尚未完成

- 真实 UI + 真实模型的 `list_page_tabs → focus_page_tab → perceive_page → close_page_tab` 验收；
- 新标签页由页面动作打开后的 watcher/admit 真实闭环；
- 大页面 `pagePerceptionPaging` 续读的真实模型验收；
- 双 target + frame/OOPIF + 旧 ref 失效矩阵；
- 用户可见画面不变的浏览器级证据（当前仅有“未调用 switch_tab”和 API 语义证据）。

在这些证据完成前，`pageTabs` 和 `pagePerceptionPaging` 在能力清单中保持 `partial`，不写成 `candidate` 或 `supported`。

## 4. 边界

- no remote push / no remote branch；
- 不修改 SDK 或发布包；
- 不把 tab API 开放给模型；模型只能通过 SDK 三个工具；
- 不接受任意 target id、任意 URL、任意 selector；
- 用户手工打开的 tab 不会自动进入工作集；
- 当前没有生产/上游发布声明。
