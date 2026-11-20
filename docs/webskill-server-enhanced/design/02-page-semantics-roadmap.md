# Octop PageAgent 与 WebSkill 页面语义对齐路线

> 状态：执行中。基线提交 `607f2074`；当前定向测试 45 passed。阶段 1 的 hover/HTML5 drag 已完成，阶段 2/3 的剩余项见下。
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

**SDK 影响**：**是（若要求跨宿主可移植）**。需要扩展 PageAgent transport 的文件选择请求/应答，
或让 host executor 提供 `pickFiles` 契约。若接受 Octop-only 旁路，则 SDK 不动但标记为宿主特有。

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

- 阶段 0：完成（45 个定向测试 + parity 文档）
- 阶段 1：部分完成（click/fill/select/set/hover/drag/submit/scroll 已有实现与定向测试；hover 已改真实 CDP 鼠标移动，drag 已优先 HTML5 DataTransfer；静默窗口仍待补）
- 阶段 2：部分完成（敏感/角色 ref 限定；隐藏/零尺寸/独立 action scope 待补）
- 阶段 3：部分完成（label/value/promotion 基础；click 探针/cursor 差分待补）
- 阶段 4：基础（tabs/target 已有；嵌套 frame 自动发现待补）
- 阶段 5-9：未开始
