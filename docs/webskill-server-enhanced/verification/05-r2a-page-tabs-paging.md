# R2A 页面能力：tab workset 与感知分页

> 状态：🟢 **pageTabs 本地 candidate 通过**；🟡 **pagePerceptionPaging 仅验证大页面 segment/cursor，continuation 尚未验收**。
> 日期：2026-11-19
> 基线：Octop `f720e979` + R0/R1；SDK `0.29.0`。
> 真实模型：本地 `qwen3.5:9b`（`http://localhost:1234/v1`，OpenAI 兼容，无鉴权）。
> 证据：`.artifacts/webskill-p1/ui/ui-result-r2a-tabs-paging.json`、`.artifacts/webskill-p1/ui/ui-result-r2a-paging.json`。

## 1. pageTabs 实现

- 后端 `src/octop/infra/control/browser/webskill/tabs.py`：
  - 只暴露 `model_targets` 工作集，不枚举用户全部标签页；
  - target id 只留在服务端，模型只看到 `tab_` 开头的随机 opaque handle；
  - focus 只改 `session.model_target`，**不调用** `browser.switch_tab`；
  - close 禁止关闭 origin/base 页；关闭后移除句柄和 target；
  - reset 只丢工作集，不关闭页面；
  - 页面动作新开的 tab 可经 `tabs/admit` 接入工作集。
- API：
  - `GET /api/browser-agent/sessions/{id}/tabs`
  - `POST .../tabs/{handle}/focus`
  - `DELETE .../tabs/{handle}`
  - `POST .../tabs/admit`
  - `POST .../tabs/reset`
- 宿主：
  - `dashboard/webskill/src/pageTabs.ts` 实现 `TabWorksetPort` / `ActionTabWatcher`；
  - `host.ts` 注入 `TabWorksetPolicy`、`pageTabs`、`pageActions.workset`；
  - `WebSkillFrame` / `browserAgentApi` 增加受控桥接。

## 2. pageTabs 真实 UI 验收

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
  "pagingSegmentObserved": true,
  "modelRequests": 15,
  "pageErrors": []
}
```

验证链路：

1. 模型调用 `list_page_tabs`，工作集只有 Origin。
2. 模型 `act_on_page` 点击 `target="_blank"` 的 Open detail；新 tab 经 watcher/admit 进入工作集。
3. 模型在同一 run 内 `list_page_tabs → focus_page_tab`。
4. 服务端 `tabs-list` 的 `active` 仍为 Origin：**focus 未改变用户可见页面**。
5. 模型在同一 run 内 `list_page_tabs → close_page_tab`；最终 API 只剩 Origin。
6. 大页面 `perceive_page` 工具结果出现 `segment`/cursor；没有继续消费 cursor，因此只算分页“产生 segment”，不宣称 continuation 完成。

## 3. pagePerceptionPaging

- `host.ts` 把 `PERCEPTION_PAGING_DEFAULTS` 注入 `ChatbotHostAdapter.pagePerceptionPaging`；不私造分段协议。
- 大页面独立验收 `scripts/webskill-p1/r2a_paging_acceptance.py`：

```json
{
  "status": "passed",
  "pagingSegmentObserved": true,
  "modelRequests": 2,
  "pageErrors": []
}
```

- **限制**：模型拿到的结果是带 `segment`/cursor 的第一段；未验证第二段、`PERCEPTION_CURSOR_INVALID`、截断提示和取消。因此能力清单保持 `partial`。

## 4. 测试

```bash
UV_NO_SYNC=1 ... pytest tests/unit/api/test_webskill_tabs.py \
  tests/unit/api/test_webskill_tabs_api.py -q
# 3 passed

npm --prefix dashboard test -- --run \
  src/pages/Control/WebSkillBrowser/pageTabs.test.ts \
  src/components/WebSkillFrame.test.tsx
# 6 passed
```

后端覆盖：

- handle 不含 target id；
- focus 后 `model_target` 改变，`browser.switch_tab` 调用为 0；
- 用户 active 页仍为 origin；
- origin 页不可关闭；
- close 调 `browser.close_tab`，不切换用户视图；
- reset 清空句柄但不关闭页面。

前端覆盖：

- 端口只显示 handle/title/url；
- focus/close 走 host bridge，不接触 CDP；
- watcher 只观察已授权工作集，并遵守 timeout。

## 5. 边界

- `pageTabs` 是本地 candidate，不是 production/上游发布。
- `pagePerceptionPaging` 仍是 partial；不能因第一段出现 cursor 就宣称大页面完整通过。
- 用户视图证据是 API `active` + 未调用 switch_tab，不是浏览器像素级录屏。
- R2A 的双 target/frame/OOPIF 矩阵沿用 P2/P3 证据；未在本轮重新做像素/人工验收。
- 未推送远端、未创建远端分支；未修改/发布 SDK。
