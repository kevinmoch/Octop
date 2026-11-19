# R2A 页面能力：tab workset 与感知分页

> 状态：🟢 **R2A 本地 candidate 通过**：pageTabs 与 pagePerceptionPaging 都有真实 UI + 真实模型证据。
> 日期：2026-11-19
> 基线：Octop `69af8b20` + R0/R1；SDK `0.29.0`。
> 真实模型：本地 `qwen3.5:9b`（`http://localhost:1234/v1`，OpenAI 兼容，无鉴权）。
> 证据：
> - `.artifacts/webskill-p1/ui/ui-result-r2a-tabs-paging.json`（tabs）
> - `.artifacts/webskill-p1/ui/ui-result-r2a-paging.json`（paging continuation）

## 1. pageTabs

### 实现

- 后端 `src/octop/infra/control/browser/webskill/tabs.py`：
  - 只暴露 `model_targets` 工作集，不枚举用户全部标签页；
  - target id 只留在服务端，模型只拿到 `tab_` 开头的随机 opaque handle；
  - focus 只改 `session.model_target`，**不调用** `browser.switch_tab`；
  - close 禁止关闭 origin/base 页；关闭后移除句柄和 target；
  - reset 只丢工作集，不关闭页面；
  - 页面动作新开的 tab 经 `tabs/admit` 接入工作集。
- API：`GET .../tabs`、`POST .../tabs/{handle}/focus`、`DELETE .../tabs/{handle}`、`POST .../tabs/admit`、`POST .../tabs/reset`。
- 宿主：`pageTabs.ts` 的 `TabWorksetPort`/watcher；`host.ts` 注入 `TabWorksetPolicy`、`pageTabs`、`pageActions.workset`；parent bridge 接入 `WebSkillFrame`/`browserAgentApi`。

### 真实验收

命令：

```bash
WEBSKILL_P1_UI_URL=http://127.0.0.1:18089 \
WEBSKILL_P1_MODEL_URL=http://localhost:1234/v1 \
UV_NO_SYNC=1 ... python scripts/webskill-p1/r2a_tabs_paging_acceptance.py
```

结果：

```json
{
  "status": "passed",
  "model": "qwen3.5:9b",
  "modelKind": "external",
  "initialTabCount": 1,
  "openedDetail": true,
  "focusKeptUserActivePage": true,
  "detailClosed": true,
  "modelRequests": 15,
  "pageErrors": []
}
```

链路：

1. `list_page_tabs` 只返回 Origin。
2. 模型 `act_on_page` 点击 `target="_blank"` 的 Open detail；新 tab 经 watcher/admit 进入工作集。
3. 模型在**同一 run** 内 `list_page_tabs → focus_page_tab`。
4. 服务端 `tabs-list` 的 `active` 仍为 Origin：**focus 未改变用户可见页面**。
5. 模型在同一 run 内 `list_page_tabs → close_page_tab`；最终 API 只剩 Origin。
6. 模型原生工具参数为 opaque handle；target id 未进入模型上下文。

## 2. pagePerceptionPaging

### 实现

- `host.ts` 注入 SDK 的 `PERCEPTION_PAGING_DEFAULTS` 作为 `pagePerceptionPaging`；不私造分段协议。
- 大页面由 SDK `PagePerceptionToolSource` 产生 `segment.index` / `cursor`；模型用 cursor 续读。

### 真实验收

命令：

```bash
WEBSKILL_P1_UI_URL=http://127.0.0.1:18089 \
WEBSKILL_P1_MODEL_URL=http://localhost:1234/v1 \
UV_NO_SYNC=1 ... python scripts/webskill-p1/r2a_paging_acceptance.py
```

结果：

```json
{
  "status": "passed",
  "model": "qwen3.5:9b",
  "pagingSegmentObserved": true,
  "pagingContinuationObserved": true,
  "cursorCallObserved": true,
  "segmentIndexes": [1, 2],
  "modelRequests": 3,
  "pageErrors": []
}
```

说明：

- 页面包含 500 个长名称控件，超过 SDK `maxChars` 预算；
- 第一次 `perceive_page` 返回 `segment.index=1` 与 cursor；
- 模型第二次 `perceive_page` 携带该 cursor，返回 `segment.index=2`；
- 没有静默截断。
- **未单独覆盖** `PERCEPTION_CURSOR_INVALID`、取消后的 cursor、以及第三段之后的行为；这些是后续 R2A 硬化项，不影响本次“segments 1→2”结论。

## 3. 测试

```bash
UV_NO_SYNC=1 ... pytest tests/unit/api/test_webskill_tabs.py \
  tests/unit/api/test_webskill_tabs_api.py -q
# 3 passed

npm --prefix dashboard test -- --run \
  src/pages/Control/WebSkillBrowser/pageTabs.test.ts \
  src/components/WebSkillFrame.test.tsx
# 6 passed
```

后端覆盖 opaque handle、focus 不改用户 active 页、origin 不可关闭、close 不调用 switch_tab、reset 不清页面；前端覆盖端口/watcher/bridge。

## 4. 边界

- R2A 是本地 candidate，不是 production/上游发布。
- 用户视图证据是 API `active` + 未调用 `switch_tab`，不是像素级录屏。
- R2A 没有重新做双 target/frame/OOPIF 的完整浏览器矩阵；沿用 P2/P3 证据。
- 未推送远端、未创建远端分支；未修改/发布 SDK。
