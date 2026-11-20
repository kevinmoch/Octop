# WebSkill 页面语义与 Octop P1 对照

> 目的：把「页面读取/操作」的语义差异从逐个手工试错，收敛成可检查的契约。
> 参考实现：`web-skill-sdk/packages/browser/src/dom/{perception,promotion,pageAction}.ts`、
> `packages/agent/src/pageAction/toolSource.ts`、`examples/browser-extension/src/content/main.ts`。
> Octop 实现：`src/octop/infra/control/browser/webskill/page_agent.py`。

## 1. WebSkill 的参考原则

### 1.1 感知是页面语义，不是 HTML dump

- 只输出 role / name / value / href / frame / provenance，不输出 class、data、注释。
- role 从显式 ARIA、input type、tag 映射；非标控件通过 role promotion 提升。
- name 的优先级：`aria-labelledby` → `aria-label` → `<label>` / 包裹 label →
  form control 的 `placeholder` / `title` → 自身直系文本 → `title`。
- **form control 的当前值不是它的名字**：textarea 的文本、combobox 的选中项必须
  放在 `value`，否则技能轨迹会把“规格不符”当成目标名。
- 隐藏节点、`aria-hidden` 节点、无布局可见尺寸的 action 句柄不进感知/操作面。
- 自定义 combobox 的值从 `data-value` / 选中项读取，不能把 value 当 name。

### 1.2 句柄是操作授权，不是选择器

- `ref` 是随机的、模型不可构造的；只能来自新鲜的 perception。
- 感知范围与操作范围分开声明；可读不等于可操作。
- 句柄跨感知保留；执行前按**当前 DOM**重新校验 role/name/范围/可见/可操作，
  不依赖“句柄还年轻”作为安全判据。
- 重渲染替换 Element 后，用 role/name/href/path/row-context 组成的签名恢复；
  复用同一个 ref，多个步骤不会因一次 React 重绘全部失效。
- 非标元素只有在提升**真的拿到 ref**时才保留提升后的 role；否则回退，避免
  `{role:"button",name:"..."}` 但 `ref:null` 的幽灵可点。

### 1.3 操作语义

- `click`：先 pointerdown/mousedown（可按 preventDefault 阻断 focus）→ focus →
  pointerup/mouseup → click；自定义下拉/菜单往往只监听 pointerdown。
- `fill`：原生 setter 写值，再派发 input/change；不能只写 `.value`。
- `select`：
  - 原生 `<select>` 按 option label/value 匹配，写值并 input/change；
  - 自定义 combobox：点击触发器 → 等 `role=listbox/grid/menu` 浮层 →
    按可访问名找 option → 只 click option（不要重复 press，浮层会吞 pointerup）→
    **回读控件**确认已选，否则报失败。
- `scroll`：从目标沿祖先链找真正在对应轴溢出的容器（根滚动容器兜底），按
  viewport 步长与重叠滚动；等待落点子树静默，而不是固定滚 300px。
- 操作结果区分 `noop` / `changes` / `navigated` / `openedTab`，不能把“打开了浮层”
  报成“选好了”。

### 1.4 技能迁移真正依赖什么

`act_on_page` 的 trace capture 不保存 ref，而是保存：

```json
{"role":"combobox","name":"问题属性","frame":null}
```

技能里的人类可读步骤也只依赖角色、可访问名、页面/帧和动作值。因此：

- **ref 随机 ID 不是迁移契约**；
- **role + accessible name（+ frame）是迁移契约**；
- 录制回放的 CDP 引用/坐标同样不是技能契约；技能必须每次重新感知。

Octop 的 `PageActionPolicy.captureArgs` 与 WebSkill 共用该 SDK 逻辑，所以只要
P1 的 `describe(ref)` 返回与页面一致的 role/name，原始 WebSkill 生成的 LLM 技能
可以在 Octop 重放；录制型技能仍应按“重新感知 + 确认动作”迁移。

## 2. Octop P1 已对齐的部分

- 感知排除 password/one-time-code/信用卡/`data-webskill-private`。
- 增加企业字段包装器（`data-field-item` / `kd-cq-field`）标题绑定；标题不再被
  提升成同名链接。
- 增加原生 label / aria 的 target name 解析（执行确认与 trace 使用）。
- 增加字段当前 value（input/textarea/select/checkbox/radio/combobox）。
- 增加自定义 combobox：按可见文字匹配 option，派发完整指针/鼠标事件，
  并通过 `Runtime.callFunctionOn` 回读确认。
- 增加通用 role promotion 的 class/id/title/tabindex/href 信号（不含扩展的
  click 监听器探针和 cursor:pointer 的完整判定）。
- action 句柄只发给可操作 role、图片取像和可滚动容器；generic 容器仍可读，
  但不发 ref。
- tab 切换优先于模型写锁；hover/resize 不抢 owner；手动点击新窗口会自动跟随 target。

## 3. 仍存在的差异（必须明确）

| 项 | WebSkill | Octop P1 | 影响 |
|---|---|---|---|
| 感知结构 | 保留 DOM 父子树 | flat elements | 模型对层级/重复控件上下文较弱 |
| 隐藏/零尺寸 | 过滤 action 句柄并隐藏节点 | 仅敏感/字段过滤，未完整做布局过滤 | 可能提供不可见控件 |
| role promotion | click 探针 + cursor 差分 + roleHints | class/id/title/tabindex/href 启发式 | 无 class 语义的纯 JS 控件可能漏点 |
| ref 恢复 | 签名恢复 + 跨感知复用 | backendNodeId + documentKey；重渲染即可能失效 | 多步 React/Vue 表单可能中途 stale ref |
| action scope | 独立于读取范围 | 当前 body 读取/操作，敏感仅部分剪枝 | 边界比参考实现宽 |
| 子树续读 / 分页 | 支持 ref 子树 + cursor | 明确 `PERCEPTION_SUBTREE_UNSUPPORTED` | 大页面只能整页读 |
| 嵌套帧 | 自动发现并可路由 | 仅当前选中帧/有限 OOPIF 路由 | 跨 iframe 控件可能读不到 |
| 动作集合 | click/fill/select/set/scroll/hover/drag/attach | click/fill/select/scroll/back | 复选框、拖拽、附件类技能无法直接迁移 |
| scroll | 找真实滚动容器 + 静默等待 | wheel at element center, fixed 300px | 虚拟列表/横向容器行为不一致 |
| 新建 Tab | tab watcher + workset | stream target activation | 已补主路径，模型侧 workset 语义仍弱 |

## 4. 技能兼容规则

1. **训练/生成技能时只写语义目标**，禁止把 `ws_...`、CSS selector、CDP backend id、
   坐标写进 SKILL.md。
2. 每个动作前必须重新 `perceive_page`；动作后重新读取验证。
3. 技能中的 target 使用 `{role, name, frame}`；同页多个同名控件必须在技能里
   补充所在区域/上下文（当前 SDK trace 只有 role/name/frame，复杂表单需模型
   在步骤描述里写清区域）。
4. 复选框使用 `set`、附件使用 `attach`、拖拽使用 `drag`；P1 当前不承诺这些，
   迁移到 Octop 前应降级为确认后的 click/fill 或明确标记不支持。
5. 录制回放只能作为 Octop 内部验证工具；跨宿主迁移必须重新生成/确认动作。

## 5. 建议的回归矩阵

| 场景 | 断言 |
|---|---|
| aria-label / labelledby / label / placeholder | node.name 来自可见标签，不是 value |
| 企业字段包装器 | textbox/combobox 名称 = 字段标题；标题不产生 link |
| 当前值 | node.value = 当前文本/勾选/选中项 |
| 自定义下拉 | select 后目标文本变化；失败时 ok=false |
| 选项浮层 | 只激活 option click；回读失败不得报 success |
| 无名称控件 | 不伪造空 name；不得提升成无 ref 的幽灵按钮 |
| 重渲染 | 同一语义目标可恢复 ref，或明确 STALE_REF |
| 新窗口 | 手动点击后 active target 跟随；Tab 仍可切换 |
| owner | hover/resize 不切 user；点击画布才 user；助手 focus 回 agent |

## 6. 结论

当前 Octop 已经与 WebSkill 在**可访问名、值、原生/自定义 select、目标身份和 tab 主路径**
上对齐；原始 LLM 技能依赖的 `role + name + value + action` 契约已可用。
剩余的 flat 树、ref 恢复、action scope、完整 promotion 和动作集合是后续 P1
增强项；在这些项完成前，不应宣称与扩展宿主 100% 等价。
