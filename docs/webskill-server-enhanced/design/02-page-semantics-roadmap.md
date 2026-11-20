# Octop PageAgent 与 WebSkill 页面语义对齐路线

> 状态：执行中。基线提交 `607f2074`；当前定向测试 73 passed；SDK 语义 parity test 1 passed；transport 4 passed。阶段 1 的 hover/HTML5 drag 已完成，阶段 2/3 的剩余项见下。
> 参考：`web-skill-sdk/packages/browser/src/dom/{perception,promotion,pageAction}.ts`、
> `packages/agent/src/pageAction/toolSource.ts`、`examples/browser-extension/src/content/main.ts`。
> 目标：把页面读取、操作和技能迁移从“逐页手工修”收敛为分阶段、可自动验收的契约。

## 0. 总原则

1. **每次动作前重新感知，动作后重新验证**；ref 永远不是技能契约。
2. 技能只保存 `{role, name, frame, action, value}`，不保存 ref、selector、坐标、CDP id。
3. 读范围（perception scope）和写范围（action scope）分离。
4. 无法达到 WebSkill 语义的能力，明确降级/拒绝，不能静默假成功。
5. 每个阶段先补测试，再改实现；阶段验收未通过不得进入下一阶段。

## 1. 阶段划分

### 阶段 0：基线冻结与契约

**范围**

- 固定当前 45 个定向测试和 parity 文档。
- 建立 WebSkill 语义矩阵：感知、动作、ref、frame、技能 trace。
- 固定“不支持能力”的拒绝行为。

**验收**

- 现有测试全绿。
- 文档明确 supported / unsupported。
- 不新增 SDK 依赖。

### 阶段 1：动作语义完全对齐

**范围**

- `click`：真实 pointer/mouse/focus 序列；不靠 `.click()`。
- `fill`：原生 setter + input/change，只读输入给出可操作提示。
- `select`：原生 select 匹配 label/value；自定义 combobox 等浮层、按可访问名点 option、回读。
- `scroll`：真实滚动容器、步长/重叠、静默窗口、`atEnd/movedBy`。
- `set`：已满足时 `noop`，否则真实切换。
- `hover`：CDP 真实鼠标移动（满足 CSS `:hover`），不是只派发合成事件。
- `drag`：HTML5 drag/drop（DataTransfer）优先，指针拖拽兜底。
- `submit`：所属 form。
- 失败/取消/超时返回结构化结果，不抛裸异常。

**验收测试**

- 现有 `test_webskill_page_actions.py` 扩展为 action × native/custom/readonly/noop 矩阵。
- 真实受控页验证：日期、问题属性、复选框、滚动容器、拖拽。

**SDK 影响**：无。SDK 已有动作定义，Octop 只实现 PageAgent 执行端。

### 阶段 2：可见性与操作范围

**范围**

- 隐藏、aria-hidden、display/visibility、零尺寸元素不发放 action ref。
- action scope 与 perception scope 分离；默认 body，敏感控件剪枝。
- 执行前按当前 DOM 复核可见性、范围、frame、文档 key。
- 隐藏元素仍可读文本但不带 ref。

**验收**

- 隐藏/零尺寸/越界 ref 测试。
- action scope 外点击/填写必须结构化拒绝。
- 不破坏当前 CCS 页面读取。

**SDK 影响**：无。SDK 的 scope 类型已存在，Octop 只装配和执行。

### 阶段 3：角色提升与可访问性

**范围**

- 对齐 `promotion.ts`：
  - tabindex / title / aria / class-id token / cursor:pointer（父级差分）
  - 页面 click 监听器探针（可选）
  - href/router 属性
  - 有可操作后代时不重复提升
- 对齐 `perception.ts`：
  - 完整 label / aria-labelledby / placeholder / title 优先级
  - value（input/textarea/select/combobox）
  - href、可滚动方向、capturable 图片
- 禁止出现无 ref 的“幽灵 button”。

**验收**

- 页面 fixture 覆盖每级信号；同名多控件；
- `test_webskill_page_semantics.py` 扩充。
- 与 extension 的 promote 决策做表驱动对照。

**SDK 影响**：**可选**。若要把 click 探针通过 remote transport 标准化，需要 SDK 扩展；Octop 也可以在 CDP 侧用 `DOMDebugger.getEventListeners` 实现，不阻塞。

### 阶段 4：帧与标签页工作集

**范围**

- 嵌套同源/跨源 frame 自动发现。
- 每个 frame 独立 perception / action scope。
- OOPIF target 路由、ref frame 归属。
- 新 tab 的 workset/admission、active target、viewer 跟随。
- 失败关闭，不跨 frame 误操作。

**验收**

- 嵌套 iframe fixture（同源、跨源、动态插入、导航后失效）。
- 新窗口/新 tab 手工点击与模型点击双路径。
- target 过期、frame 失效明确报码。

**SDK 影响**：无。SDK 已有 frame scope、nestedFrames 和 tab workset 契约；Octop 补路由。

### 阶段 5：附件（attach）协议

**范围**

- 文件选择必须由用户手势触发；模型不能传本地路径。
- host 选择文件后将 bytes 写入远端 `<input type=file>`（DataTransfer）。
- 读取/传输/大小/类型/取消/超时校验。
- 权限、审计、记忆授权。

**验收**

- 选择、取消、超大文件、非 file input、跨用户隔离。
- **需要 SDK 协议扩展**：当前 remote `PageAgentRequest.execute` 不带文件通道，
  `createRemotePageActionExecutor` 也没有 `pickFiles`。若只做 Octop 私有桥，可不改 SDK，
  但该 attach 不可复用、也不算与扩展宿主等价。

**SDK 影响**：本节最初判断是可移植方案必须改 SDK；实际按用户决策采用 **Octop-only**：
host 侧选择文件 → `/attachments` API → PageAgent `DataTransfer`，SDK 未修改。
因此该能力可迁移性仅限 Octop，不能宣称与 extension 等价。

### 阶段 6：树、子树与分页

**范围**

- 感知从 flat elements 改为 DOM 树。
- 子树续读（ref）与 cursor/paging。
- 大页面不整页塞入上下文。
- tree 节点仍只含 role/name/value/href/frame/capturable。

**验收**

- 树形快照测试；同 ref 子树是整页子集。
- 分页边界、节点计数、字符预算。
- 不返回不可操作锚点 ref。

**SDK 影响**：无。SDK 已有 `PerceivedNode.children`、subtree、paging 协议；Octop 实现 reader。

### 阶段 7：Ref 签名恢复增强

**范围**

- 当前已有 path/tag/role/name 恢复。
- 增加 href、form name、最近行/列表上下文、frame。
- 路径改变时按签名唯一恢复；歧义必须拒绝。
- 动作后 `recovered` 标记（SDK outcome 已有字段，Octop 回填）。

**验收**

- 重渲染替换、列表重排、同名控件、跨 frame。
- 恢复失败必须 `PAGE_ACTION_STALE_REF`，不得恢复到错误目标。

**SDK 影响**：无。

### 阶段 8：技能兼容与迁移验收

**范围**

- 固化 `act_on_page` trace target：`{role,name,frame}`，绝不写 ref。
- 建立 WebSkill 生成技能 fixture，在 Octop 上重放：
  - 只读列表、表单填写、自定义下拉、复选框、多步 Tab。
- 明确录制回放（CDP ref）不是迁移路径。
- unsupported 动作（attach/不支持 frame）在迁移前显式拒绝。

**验收**

- 同一语义技能在 extension/Octop 两个执行器上得到相同 target/outcome。
- 不依赖 ref 的 SKILL.md 可直接运行。
- 失败时给出可操作的迁移诊断。

**SDK 影响**：无（trace capture 已有）；若发现协议字段缺失，按阶段 3/5 的 SDK 变更处理。

### 阶段 9：全量验收与发布闸门

**范围**

- 真实 CCS 页面矩阵：
  - 现场巡检问题表单
  - 工程管理 Tab
  - 工作面移交计划表格
  - 自定义下拉/日期/复选框/滚动
- 性能：单次感知/动作延迟预算。
- 安全：敏感字段、越权、跨用户、脚本注入。
- 文档、回归测试、阶段报告。

**验收**

- 所有自动化测试通过。
- 手工只做最终业务确认，不做逐控件调试。
- 明确发布边界和不支持项。

**SDK 影响**：无。

## 2. SDK 改动汇总

| 阶段 | 是否必须改 SDK | 原因 |
|---|---|---|
| 1 动作语义 | 否 | SDK 已有动作，Octop 执行端补齐 |
| 2 可见性/scope | 否 | SDK 已有 scope 类型 |
| 3 角色提升 | 可选 | click 探针可 CDP 实现；标准化才改 SDK |
| 4 帧/标签页 | 否 | SDK 已有 frame/tab 契约 |
| 5 attach | **是**（可移植方案） | remote transport 无文件选择通道 |
| 6 树/子树/分页 | 否 | SDK 已有 tree/paging 契约 |
| 7 ref 恢复 | 否 | 宿主实现 |
| 8 技能迁移 | 否 | trace target 已存在 |
| 9 全量验收 | 否 | 测试/文档 |

**结论：严格必须改 SDK 的只有阶段 5，且仅当要求 attach 跨宿主可移植。**
阶段 3 的 click 探针是可选 SDK 扩展；其余全部在 Octop 宿主/后端完成。

## 3. 阶段执行顺序与门禁

```text
0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9
```

- 阶段 1/2/3 可以在现有 P1 页面语义层内连续落地。
- 阶段 4/6 是结构性改造，先冻结旧 reader，再以兼容开关切换。
- 阶段 5 若决定不改 SDK，必须把 attach 明确标为 Octop-only，不能声称与 extension 等价。
- 每个阶段结束必须：
  1. 新增或更新测试并跑绿；
  2. 更新本文档状态；
  3. 记录与 WebSkill 的剩余差异；
  4. 才允许进入下一阶段。

## 4. 当前状态

- 阶段 0：完成（70 个定向测试 + parity 文档）
- 阶段 1：完成（click/fill/select/set/hover/drag/submit/scroll 均有执行级测试；fill 使用原生 setter；hover 走真实鼠标；drag 优先 HTML5；scroll 有静默窗口）
- 阶段 2：完成（aria-hidden/hidden/inert/display/visibility、零尺寸、独立 actionScope include/exclude；执行前再次校验可见性与 action scope）
- 阶段 3：完成（label/value/href、cursor:pointer 父级差分、DOM click 探针、actionable descendant 抑制均已实现并测试；scrollable 方向作为后续元数据增强）
- 阶段 4：完成（同源嵌套 frame 自动发现、嵌套 frame refs、投影选择、OOPIF、动态导航失效、target workset 已有测试，共 20 项 frame/target 相关断言）
- 阶段 5：完成 Octop-only 版本（host 文件选择 → attachments API → PageAgent DataTransfer；SDK 未改）
- 阶段 6：完成（树形 children、ref 子树、客户端 paging 输入树已具备；真实页面 roots=63/flat=83）
- 阶段 7：完成（path/tag/role/name/href 唯一恢复；歧义拒绝）
- 阶段 8：完成（SDK `createDomPerceptionReader` 原生控件 fixture 与 Octop 期望 role/name 一致；target 契约无 selector/坐标）
- 阶段 9：完成自动/只读矩阵（现场巡检问题、工程管理；真实动作矩阵；release gate；完整业务写入回归仍建议上线前执行）


## 5. 阶段 1/2 验收证据（本轮）

- 自动化：`test_webskill_page_actions.py` 覆盖 click / fill（原生 setter + readonly）/
  custom select 回读 / set 切换与 noop / hover 真实鼠标 / drag 指针兜底与 HTML5 /
  submit / scroll 回读。
- 自动化：`test_webskill_page_semantics.py` 覆盖 action scope 子树/排除、
  aria-hidden/hidden 祖先、零尺寸/无布局 action ref、path 改变后的唯一签名恢复。
- 真实受控页：
  - `问题属性` select → `规格不符`，target `{role:"combobox", name:"问题属性"}`；
  - `问题描述` fill → `测试`，target `{role:"textbox", name:"问题描述"}`；
  - `问题属性` hover → ok；
  - 页面回读值保持 `规格不符 / 监理 / 测试`。
- 结果：54 个定向测试通过，ruff/mypy 通过。

阶段 2 的已知宿主边界：P1 当前把 perception scope 同时作为 action ref 的发放范围；
SDK 的 `PageActionPolicy.scope` 未通过 remote execute 协议下传，因此“独立 actionScope”
尚未形成可配置的双范围；这属于宿主协议扩展，不是本次 SDK 修改。

## 6. 阶段 3 验收证据

- cursor:pointer 使用父级差分，避免把继承 pointer 的容器全部提升。
- DOM click 探针通过 CDP `includeCommandLineAPI` 的 `getEventListeners` 标记。
- 有 actionable descendant 的容器不再重复提升。
- 真实受控页感知：83 nodes / 44 targets，70ms；`问题属性/问题提出方/问题描述`
  的 role、name、value、ref 均正确，owner 保持 agent。


## 7. 阶段 4 验收证据

新增 `test_webskill_frame_semantics.py`：

- `discover_frame_routes` 递归进入 `contentDocument`，得到
  `iframe#outer` 与 `iframe#outer >>> iframe#inner`；
- `nested_frame_refs` 只返回所选 frame 的直接子 frame；
- `projection_document` 能投影到内层 frame，节点路径以该 frame 文档为根。

已有 `test_webskill_page_agent.py` 覆盖同源 frame 读取/动作、子 frame 导航失效、
OOPIF attach/detach/导航、target workset、new target 激活。


## 8. 阶段 5 验收证据（Octop-only）

- 新增 `POST /api/browser-agent/sessions/{id}/attachments`：base64、单文件 10MB、
  总量 20MB、同源/session/user 校验、审计只记录文件名/类型/大小。
- `PageAgent.attach`：只接受 `<input type=file>`，用 `DataTransfer` 写入并 dispatch
  input/change；执行后清空 pending files；无文件/非 file input 结构化失败。
- `WebSkillFrame`：文件选择 Modal（中英双语），由用户手动选择。
- `OctopPageAgentTransport`：attach 前通过 `attach-files` bridge 请求文件，父窗口上传后才 grant/execute。
- 测试：attachments HTTP 上传/非法 base64、attach action、transport 调序与取消。
- 真实隐藏 file input 验收：`title=测试附件`、`display:none`，被额外暴露为
  `textbox` target；上传 `proof.txt` 后 attach 返回 ok，页面 `files[0]` 为
  `proof.txt` / 12 bytes。


## 9. 阶段 6 先行证据（子树读取）

- `perceive_page` 接受 `ref`，只返回该 ref 的后代元素；不再整页回落。
- ref 未知/过期返回 `PERCEPTION_SUBTREE_UNKNOWN_REF`，跨 frame 返回
  `PERCEPTION_SUBTREE_OUT_OF_SCOPE`。
- 测试：`test_subtree_reads_only_the_referenced_subtree` 验证全页 ref → 子树只含该目标，
  以及缺失 ref 的结构化错误。
- 树形 `children`、cursor/paging 仍未实现，阶段 6 不宣称完整。


## 10. 阶段 6/7 验收（本轮）

- 感知输出改为 `PerceivedNode` 树：真实页面 roots=63、flat=83，
  targets=48；字段 role/name/value/ref 保持正确。
- `perceive_page(ref=...)` 只返回该子树；未知 ref / 跨 frame 有结构化错误。
- 分页由 SDK 的 `segmentPerception` 处理，P1 负责提供合法树。
- ref 恢复支持 path 不变、path 改变后按唯一 role/name/href 恢复；
  同名歧义返回 `PAGE_ACTION_STALE_REF`，不猜测目标。
- 测试：tree nesting、subtree、ambiguous recovery、semantic target shape。
- 技能 target 契约测试断言只含 `ref/role/name`，不含 selector/坐标。


## 11. 目标身份修正（阶段 8 前置）

执行结果的 target 现在优先使用 ref signature 的 role/name。此前 promoted div 在感知中是
`button/测试悬浮`，执行回填却是 `div/悬浮`，会把 trace 技能身份改坏。真实动作矩阵
现在回填：

```text
checkbox 勾选
button 测试悬浮
button 测试滚动
button 测试拖拽
```

## 12. 阶段 9 真实矩阵（本轮）

受控页面临时元素，清理后不影响业务数据：

| 动作 | 结果 |
|---|---|
| set checkbox true | checked=true |
| hover | mouseover=2 |
| scroll down | scrollTop=30, movedBy=30, atEnd=false |
| drag HTML5 | dragstart=1, drop=1 |
| attach 隐藏 file input | files[0]=proof.txt, 12 bytes |
| select 问题属性 | 回读 规格不符 |
| fill 问题描述 | 回读 测试 |

阈值差异：scroll 的可滚动内容高度只有 180px，步长按 `max(extent-64, extent/2)`
得到 30px，因此 `movedBy=30` 是预期，不是固定 300px。


## 13. 阶段 8/9 证据

- `semanticParity.test.ts` 直接加载 WebSkill SDK browser dist 的
  `createDomPerceptionReader`，在 jsdom 中读取同一组 button/input/link fixture，
  得到 `{button,保存}`、`{textbox,搜索}`、`{link,详情}`，与 Octop 期望一致。
- `test_webskill_release_gate.py` 固定动作面、target 形状和 tree 兼容性。
- 真实动作矩阵结果见 §12。
- 已知不可自动化：真实 CCS 业务页面的完整回归仍需要上线前人工走查；
  本轮已覆盖表单、下拉、树、隐藏 file input、checkbox、hover、scroll、drag。


## 14. 阶段 2 独立 actionScope 验收

- `perceive` 可携带独立 `actionScope`；未携带时保持旧的 perception scope 行为。
- action ref 只从 actionScope 发放；perception 仍读取整个 include 范围。
- 执行前 `_assert_ref_in_action_scope` 重新查询当前 DOM，目标越界返回
  `PAGE_ACTION_OUT_OF_SCOPE`。
- 真实页面：
  - 正常 `actionScope={include:["body"]}` → 46 targets；
  - 限制 `actionScope={include:["#does-not-exist"]}` → 0 targets；
  - select 问题属性仍 ok。
- Transport 默认向 perceive 注入 `{include:["body"]}`，host 配置与
  `PageActionPolicy.scope` 可以各自装配。


## 15. 业务页面只读矩阵（阶段 9）

- 现场巡检问题：81 nodes / 46 targets，owner=agent。
- 工程管理：214 nodes / 188 targets，读取成功，无写操作。
- 切回现场巡检问题：81 nodes / 46 targets，页面恢复。
- 无 SDK 改动。

## 16. 阶段 10：可滚动容器语义

目标：对于无可访问名称、无 tabindex 的可滚动 generic 容器，也能给出
`scrollable: ["left","right","up","down"]` 与可操作 ref，使 `scroll` 动作
能够指向真实容器，而不是只能滚最近祖先。

验收：
- 元素自身 overflow 轴有溢出且样式允许滚动；
- 方向按当前 scrollLeft/scrollTop 只报告还能继续的方向；
- 不可见容器不发 ref；
- scroll 动作后回读 movedBy/atEnd。


## 17. 阶段 10 验收：可滚动 generic 容器

- 新增 scrollable 探针：按元素自身 scrollTop/scrollLeft 与 overflow 轴
  产出 `left/right/up/down`，只报告仍可继续的方向。
- DOMBuilder 会压平无名 generic 容器；现在 `_scrollable_elements` 把它们补回，
  并发放 scroll ref。
- 真实 generic 容器（无 role/name/tabindex）：
  - 感知节点 `{role:"generic", scrollable:["down"], ref:...}`；
  - scroll down → `ok:true`，`movedBy:35`，`scrollTop:35`。
- 不可见滚动容器不发放 ref。
- 测试：`test_scrollable_elements_are_not_dropped_when_they_have_no_role`、
  `test_scrollable_probe_reports_backend_directions`。


## 18. 阶段 11：性能、可观测性与 smoke

- execute 审计增加 `durationMs`；perceive 输出结构化日志：
  `session / duration_ms / nodes / targets`，不记录页面文本。
- 修复无意义等待：只有 click/submit 等待新 tab；fill/select/hover/set/scroll
  不再白等 1.2s。
- 新增 smoke 脚本 `scripts/webskill-p1/page_semantics_smoke.py`：
  真实仪表盘登录态 → 创建 session → 感知 → select/fill/hover（同值，不发起业务写变更）
  → 输出 JSON；失败退出非 0。
- 真实 smoke：
  - perceive 81ms，81 nodes / 46 targets；
  - select 410ms，fill 38ms，hover 22ms；
  - owner=agent。
- 动作审计测试断言 `durationMs` 为非负整数。
