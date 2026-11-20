# Octop PageAgent × WebSkill 0.29.0 能力对照与 verify 升级建议

日期：2026-11-20  
对照基线：`/Users/mochunhui/Git/web-skill-sdk` 当前 **0.29.0**  
Octop 范围：P1 PageAgent / 宿主；不包含 SDK 的 skill 管理、文档渲染等全部产品面。

> 状态标记：✅ 已对齐；🟡 部分对齐；❌ 未实现；➖ 宿主特有/不适用。

## 1. 先说结论

在本矩阵的 **48 项页面语义、动作和技能迁移能力**中：

```text
✅ 完全对齐   38 项  79%
🟡 部分对齐    8 项  17%
❌ 未实现      1 项   2%
➖ 宿主特有     1 项   2%
```

这不是“Octop 等于整个 WebSkill SDK”。它只说明 PageAgent 相关面：
**页面读取、页面操作、目标身份、技能迁移**。SDK 的 Console、Governance、
文档渲染、技能生命周期等不在本矩阵内。

## 2. 感知能力矩阵

| # | 能力 | 状态 | Octop 实现/证据 | 差距 |
|---:|---|---|---|---|
| 1 | 树形 `children` | ✅ | `_nest_nodes`；真实页 roots=63/flat=83 | — |
| 2 | 原生 role 映射 | ✅ | `_role` + DOMBuilder + action refs | — |
| 3 | label/aria/placeholder/title 名称优先级 | ✅ | `_field_controls`、`_actual_target`、字段测试 | 复杂 aria 组合仍需真机回归 |
| 4 | input/textarea/select/checkbox/radio 当前 value | ✅ | `_live_value` | — |
| 5 | 隐藏/零尺寸不发放 action ref | ✅ | `_action_visible`、`_hidden_by_ancestor` | — |
| 6 | 敏感/私有子树排除 | ✅ | `_sensitive_subtrees`、scope exclude | — |
| 7 | 独立 actionScope | ✅ | `actionScope` + `_assert_ref_in_action_scope` | host UI 仍需配置入口 |
| 8 | 链接绝对 href | ✅ | `_absolute_href` + `urljoin`；测试覆盖相对 URL/query | — |
| 9 | 页面图片 capturable / capture | ✅ | `_image_elements`、`capture_image` | — |
| 10 | scrollable directions | ✅ | `_scrollable_backends`、`_scrollable_elements` | 只做自身容器，不上溯 |
| 11 | cursor:pointer 提升 | ✅ | 父级差分探针 | 不做 CSS 级 hover 差分 |
| 12 | DOM click listener 探针 | ✅ | `getEventListeners` 标记 | 依赖 CDP 上下文 |
| 13 | actionable descendant 抑制 | ✅ | `_has_actionable_descendant` | — |
| 14 | 模态提升 | ✅ | 动作后模态快照 + `elevatedModal`；真实 dialog 验证通过 |
| 15 | 嵌套 frame 自动发现 | 🟡 | 递归 `discover_frame_routes`、OOPIF | 读取仍需按 frame 选择，不是一次全树 |
| 16 | modal/frame provenance | 🟡 | modal provenance + frame 字段；inline-frame provenance 未做 |
| 17 | 子树续读 | ✅ | `perceive_page(ref=...)` | — |
| 18 | paging | ✅ | SDK `segmentPerception` 消费 Octop 树 | 没有 Octop 侧 cursor UI |
| 19 | ref 跨感知复用 | ✅ | `PageRef` signature 复用 | — |
| 20 | path 改变后的恢复 | 🟡 | 唯一 role/name/href 恢复 | 未覆盖行/列表上下文签名 |
| 21 | 同名歧义拒绝 | ✅ | 唯一匹配才恢复，否则 stale | — |
| 22 | 隐藏节点从读取树剪除 | 🟡 | 只剪 action ref，节点仍可能保留 | 与 WebSkill 的剪枝不完全一致 |

## 3. 动作能力矩阵

| # | 能力 | 状态 | Octop 实现/证据 | 差距 |
|---:|---|---|---|---|
| 23 | click | ✅ | CDP 真实指针序列 | — |
| 24 | fill | ✅ | 原生 setter；controlled Vue textarea 用 select-all + key + 回读 | — |
| 25 | 原生 select | ✅ | `interact.select_value` | — |
| 26 | 自定义 combobox | ✅ | click + option + 回读 | — |
| 27 | set + noop | ✅ | aria/checked 回读；已满足返回 noop | — |
| 28 | hover | ✅ | CDP 真实鼠标移动 | 纯 CSS `:hover` 已可；浮层内容仍需重新感知 |
| 29 | drag | ✅ | HTML5 DataTransfer 优先，指针兜底 | 仅起终点，无轨迹 |
| 30 | submit | ✅ | `requestSubmit()` | — |
| 31 | scroll | ✅ | 真实容器、步长、静默窗口、atEnd/movedBy | — |
| 32 | attach | 🟡 | Octop 文件桥 + DataTransfer；真实隐藏 input 已验证 | 不是跨宿主协议；SDK/extension 用系统选择器 |
| 33 | back | ✅ | navigate.go_back | — |
| 34 | noop / changes 元数据 | 🟡 | set 与 click 有 noop；mutation added/removed、status 文本、navigated；status 会脱敏动作值 | 其他动作的 noop 判定仍简化 |
| 35 | recovered outcome 标记 | ✅ | 恢复 ref 带 `recovered=True`，execute 回填 outcome | — |
| 36 | 模态动作提升 | ✅ | elevated modal ref 绕过 actionScope；真实弹窗内按钮点击成功 |
| 37 | 确认/授权/审计 | ✅ | PageActionPolicy + host UI + audit duration | — |
| 38 | tab workset / 新页 | ✅ | target admit/switch、stream 跟随 | — |
| 39 | 下载信号 | 🟡 | page_agent 有 downloads 基础设施 | 未在本轮矩阵做完整动作级回归 |
| 40 | frame 内动作 | ✅ | frame/OOPIF 测试 | 跨 frame drag 明确拒绝 |
| 41 | attach 跨宿主可移植 | ❌ | Octop-only | 需要 SDK 文件选择协议 |
| 42 | 执行 target 语义身份 | ✅ | ref signature 回填 role/name，trace 无 selector/坐标 | — |

## 4. 技能迁移/宿主矩阵

| # | 能力 | 状态 | 证据 |
|---:|---|---|---|
| 43 | trace target `{role,name,frame}` | ✅ | `test_webskill_release_gate.py`、SDK parity test |
| 44 | trace 不含 ref/selector/坐标 | ✅ | `_target` 契约测试 |
| 45 | SDK 原生控件语义 parity | ✅ | `semanticParity.test.ts` 直接加载 SDK browser dist |
| 46 | 企业自定义控件双执行器实测 | 🟡 | SDK 原生 fixture；未跑真实 extension 全链路 |
| 47 | 录制回放可迁移 | ➖ | Octop 录制回放是宿主内部机制，不是 WebSkill 技能契约 |
| 48 | 文档/附件读取 | ✅ | 既有 readers、linked documents、PDF/DOCX/XLSX 测试 |

## 5. verify 套件是否过期

**是，明显过期。**

证据：

- `verify/agent/00-overview.md` 写覆盖 `@webskill/sdk 0.12.0` + console `0.9.0`；
  当前发布线是 SDK **0.29.0** / chatbot **0.26.0** / console **0.22.5**。
- A-03 仍断言 console 版本 `0.9.0`。
- A-70 仍写“六个动作”：click/fill/submit/select/set/attach；
  当前动作面还包括 `scroll` / `hover` / `drag` / `back`。
- A-57 的宿主感知用例仍是 0.11/0.12 的三帧形态；没有覆盖树、子页、ref 恢复、
  独立 actionScope、scrollable generic。
- 0.13–0.29 的大量新能力，包括附件流式处理、hover/drag、scroll、旧 XLS、
  流式工作簿、ECharts 等，没有对应到当前 verify 用例。
- verify 的页面语义部分仍有价值，但不能作为“当前版本完整验收”的依据。

## 6. 建议的 verify 升级方案

不要重写 229 条旧用例；保留它们为 legacy 基线，追加一个 **delta 套件**，
并先修少量会直接误判的旧用例：

### 6.1 必改旧用例

- A-03：版本号改为本轮 `console 0.22.5`（或从页面读版本并对照当前发布线）。
- A-70：动作集合从 6 扩到当前动作面，并补 scroll/hover/drag 的确认卡判据。
- `verify/agent/00-overview.md`：覆盖版本改为 0.29.0 / 0.26.0 / 0.22.5。
- 引入章节：把 0.12.0 旧用例标为 `legacy`，新增 `0.29.0 delta` 章节。

### 6.2 新增 delta 用例

| 新编号 | 场景 | 对应 Octop 行 |
|---|---|---|
| A-354 | 树形 children 与角色/名称/值 | 1–4 |
| A-355 | 隐藏/零尺寸不发放 action ref | 5 |
| A-356 | perception scope 与 actionScope 独立 | 7 |
| A-357 | cursor/click listener 提升 | 11–13 |
| A-358 | scrollable generic 容器 | 10 |
| A-359 | 子树续读与分页 | 17–18 |
| A-360 | ref 跨感知复用/恢复/歧义拒绝 | 19–21 |
| A-361 | set/hover/drag 的效果与确认卡 | 27–29 |
| A-362 | target 语义身份不含 selector/坐标 | 42–44 |
| A-363 | attach 的宿主能力边界（SDK 系统选择器 vs Octop host bridge） | 32/41 |

### 6.3 版本与执行策略

1. 先新增 delta，不改旧判据对象；
2. 旧 A-03/A-70 修正版本和动作集合；
3. 真实 LLM 只跑 delta + 被修旧用例，其余按 legacy 抽样；
4. 对 Octop 单独运行 `business_matrix_smoke.py` / `page_semantics_write_smoke.py`；
5. 跨宿主 attach 作为独立 SDK 协议工作项，不混进 verify 页面用例。

## 7. 本次 verify 环境已跑的页面语义用例

- A-57 感知白名单 — PASS
- A-58 敏感排除/提示注入 — PASS
- A-64 fill 确认与回读 — PASS
- A-70 fill / click 确认 — PASS
- A-70 attach 确认 + 系统文件选择器 — PASS

详见 [14-sdk-verify-environment.md](14-sdk-verify-environment.md)。

**没有临时修改 SDK 代码**；验证时的 SDK 工作区已恢复干净。

## 8. verify delta 已落地

`/Users/mochunhui/Git/web-skill-sdk/verify/agent/30-page-semantics-0.29.md` 已新增
A-354 ~ A-363；`00-overview`、`01-environment/A-03`、`09-page-actions/A-70` 和
`verify/README.md` 已加 0.29.0 delta 注解。旧 229 条仍作为 legacy 基线，不声称整体已升级。


## 9. 模态提升验收

真实页面临时 dialog：

```text
button 测试弹窗按钮 → dialog aria-label=测试弹窗
```

动作结果：

```json
{"ok":true,"target":{"role":"button","name":"测试弹窗按钮"},"elevatedModal":"测试弹窗"}
```

随后用限制 actionScope `{include:["#does-not-exist"]}` 感知：

```json
{"role":"button","name":"弹窗内按钮","provenance":"modal-elevated","ref":"ws_..."}
```

弹窗内按钮动作成功，证明 elevated modal ref 会绕过普通 actionScope，
但普通范围外目标仍然拒绝。


## 10. 最新增量验收

- `changes.status`：从固定 `[role=status]/[role=alert]/.messages` 状态区读取，最多 200 字符；
  动作值在状态文本中替换为 `[redacted]`。
- 模态提升：真实 dialog 的 outcome 带 `elevatedModal`；感知节点带
  `provenance: "modal-elevated"`；限制 actionScope 时弹窗内 ref 仍可执行。
- 当前定向测试：79 passed。


## 11. 本轮测试

```text
Python 定向测试 80 passed
SDK verify slug 7 passed
ruff / mypy 通过
```


## 12. 页面录制技能的语义回放证明

新增：

- `scripts/webskill-p1/fixtures/recorded-page-skill.json`
- `scripts/webskill-p1/semantic_skill_smoke.py`
- `tests/unit/api/test_webskill_skill_migration.py`

fixture 只含：

```json
{"role":"combobox","name":"问题属性"}
{"role":"textbox","name":"问题描述"}
```

不含 ref、selector、坐标。脚本每一步重新 `perceive_page`，按 role/name
解析新鲜 ref，再 grant/execute。

真实 CCS 页面结果：

```text
select 问题属性 → 规格不符  ok
fill 问题描述 → 测试      ok
changes: select added=1/removed=1, fill added=1/removed=0
```

这验证了 WebSkill 风格技能所依赖的语义目标契约在 Octop 可执行。

注意：这是**语义步骤回放 harness**，不是完整的 SDK SKILL.md 解析器；
技能仍由 Chatbot/模型按步骤调用 `perceive_page` + `act_on_page`。


## 13. 真实安装 SKILL.md 的执行验证

通过 Octop 控制台 › 安装与导出安装了一个最小 prompt-only 技能：

`scripts/webskill-p1/fixtures/recorded-inspection-skill/SKILL.md`

技能只指示模型：

- `perceive_page`
- 按 `role=combobox, name=问题属性` 选择 `规格不符`
- 按 `role=textbox, name=问题描述` 填入 `测试`
- 不保存、不提交

真实 Octop Chatbot 执行结果：

```text
技能：recorded-inspection-page
问题属性 = 规格不符
问题描述 = 测试
上下文 28.2k / 1049k
```

模型按语义目标执行，未使用 selector/坐标。运行中确认卡未出现，是因为该 origin+action
已在此前测试中留下“不再询问”记忆；A-64/A-70 已单独验证确认卡路径。


## 14. Trace-backed 技能候选实测

使用 SDK `SkillGenerator` 的 trace-backed 校验路径生成候选（draft 内容为固定
页面流程；trace records 包含 `perceive_page` 和两次 `act_on_page`）：

- 候选名：`recorded-inspection-trace`
- 版本：1.0.0
- 只依赖 `perceive_page` / `act_on_page` / `role+name`
- 生成器返回 `submitted`，候选通过 trace 工具名校验

把该 SKILL.md 安装到 Octop 后，真实模型执行：

```text
Step 1 combobox 问题属性 select 规格不符  → ok
Step 2 textbox 问题描述 fill 测试          → ok
没有保存/提交/审批
```

结果：

```text
问题属性：规格不符
问题描述：测试
```

这验证了 trace-backed 页面技能的候选格式、安装链路和 Octop 执行链路；
候选正文目前是固定 draft，不是由真实 LLM 根据 trace 自动撰写。


## 15. 真实 LLM 从 trace 生成技能并执行

Octop 中开启「技能自动生成」后，先用模型执行页面动作：

```text
select 问题属性 = 规格不符
fill   问题描述 = 测试
```

随后模型调用 `generate_skill`，真实生成候选：

```text
name: fill-inspection-issue-form
candidateId: cand-t7aj7oaa
```

生成的 SKILL.md 被安装回 Octop，真实模型再次执行：

```text
问题属性 = 规格不符  confirmed
问题描述 = 测试      confirmed
```

没有保存/提交/审批。

这是当前最完整的链路：

```text
真实页面动作
→ trace
→ 真实 LLM 生成 SKILL.md
→ Octop 安装
→ 真实模型按技能执行
```

### 发现的 trace 校验缺口

生成确认卡同时展示了「技能里写的步骤」与「本会话实际执行」：

- 第二个 `act_on_page` 技能声明的参数是 `action=fill`
- trace 实际显示的是 `action=select`

生成器仍然提交了候选，因为当前校验只要求声明的**工具名**在 trace 中出现，
没有逐参数核对。这个缺口需要在后续单独修正，不能把本次生成视为
参数级完全 trace-backed。
