# Octop 服务端增强版设计

> 状态：🟢 P0/P1 已完成（candidate）；🟡 P2 实施中；实际支持能力以对应 FR/AC 的真实证据为准。
> 需求与编号见 [../00-requirements-overview.md](../00-requirements-overview.md)。

## 1. 设计目标

将 Octop 的真实 Chromium 与 WebSkill 的 Agent、技能和 UI 通过受限 PageAgent 通路连接，避免旧控制器网关、专用 iframe 扩展和目标网站代理。

本设计优先复用已有组件，不把 octop-browser 的浏览器/录制能力复制到 Octop，也不为 Octop fork Chatbot/Console。

## 2. 通道与双指针

```text
画面流 ───────────────► viewTarget
视图内输入 ───────────► viewTarget
PageAgent ────────────► modelTarget
模型请求 ─────────────► 客户端配置的模型服务
```

- `viewTarget`：用户实际观看和输入的 target。
- `modelTarget`：适配层持有的模型逻辑目标，驱动 `envelope.targetId`。
- 用户切换标签只改变 viewTarget；显式“跟随/设为助手目标”才对齐 modelTarget。
- 模型动作打开的 target 可改变 modelTarget，但不能改变 viewTarget。
- target 关闭时指针失效，不自动落到未授权 target。

### 状态机

```text
初始化：viewTarget = modelTarget = 用户选择页
用户切页：viewTarget 改变，modelTarget 不变
模型新页：modelTarget 改变，viewTarget 不变
显式跟随：二者按用户动作对齐
人工输入/接管：模型写入停止，人工写权优先
断线：在途请求失败，写操作不自动重放
关闭：ref、grant 和工作集句柄失效
```

## 3. 多 target 与 frame

### 3.1 octop-browser 会话

模型操作其他 target 时使用 `Target.attachToTarget(flatten=true)` 的 scoped session，不调用现有 `switch_to_target`，避免重连、activate 和整表清空影响用户视图。共享底层 CDP 连接通过 `session_id` 路由，attach 生命周期结束后 detach。

- 每个模型目标有独立的 document/ref 绑定和默认 execution context，不污染 Octop 共享 `BrowserSession` 的 ref cache。
- profile 级锁/lease 协调写入，但不共享“当前模型 target”状态到 UI。
- target 的关闭、导航和 Chrome 崩溃都显式清理。
- OOPIF 通过 iframe 的 `DOM.describeNode.frameId` 与 scoped target 的根 frame 绑定，同时核对 parent、target URL 与当前 URL（含 query）；不使用会停留在历史地址的 `src` 属性或全浏览器 URL 猜测。

### 3.2 文档身份

- 一次感知应答只带一个顶层文档 key。
- 服务端维护 user/profile/target/loader/URL，以及每个 ref 对应的 frame、loader、execution context。
- SPA URL 变化使顶层文档 key 改变。
- 子 frame 独立导航至少使该 frame 的旧 ref 失效。
- `PageAgentTargetEntry.frame` 只用于现有展示/引用，不承担安全身份。

### 3.3 ref 与 frame 投影

- 以 backendNodeId 保持节点身份，刷新 nodeId。
- ref 在顶层文档内唯一，不允许跨 frame 同名 ref 冲突。
- 使用现有客户端 frameRouter 聚合，不在服务端再造一个聚合器。
- 未授权 frame 使用现有 FrameNote：`reason: 'not-found'` 仅为汇总类别，`message` 固定为 `This frame is outside the allowed perception scope.`。
- 不增加协议外说明节点；客户端只渲染一次并写审计。
- 如果该分类语义不可接受，需回需求评审新增枚举，不得私自扩展公共契约。

### 3.4 选择器

限制长度、节点数、frame 数、嵌套层数与执行时间；禁止未评审的高开销伪类和脚本入口。使用成熟 CSS 解析依赖，不手写正则协议解析器。超限明确报错，不静默截断。

## 4. 权限、grant 与人工接管

### 4.1 普通动作 grant

客户端顺序：

```text
感知 → 范围检查 → 用户确认 → 客户端申请 grant → 执行 → 核销/审计
```

绑定用户、会话、文档、target、frame、ref、动作、完整规范化载荷摘要和有效期。它不是用户同意的密码学证明，只用于防重放、串号、过期和陈旧文档。

### 4.2 回放步 grant

- 由 Octop 服务端在有效回放会话内自行签发，不要求客户端预签。
- 只证明当前步骤仍在有效回放范围内，不证明用户逐步同意。
- 绑定 replaySessionId、stepId、当前文档、target 和规范化动作载荷。
- 每步独立、一次性核销；接管/取消/崩溃/过期后不得继续使用。
- 服务重启后旧回放会话与 grant 全部失效。

### 4.3 回放会话

沿用现有 `/browser/record-replay/replay` 的入口，增强后返回/维护 `replaySessionId`、recordingId、用户、profile、起始 target、范围、最大步骤数、有效期和状态。

- 会话状态在服务端内存中，崩溃后不恢复。
- 用户开始回放时确认一次范围，不将其解释为无限期或无限动作授权。
- 当前步骤开始时才计算该步 grant TTL，避免长时间回放中的陈旧授权。
- 不修改 SDK 的 `PageActionRequest`。

### 4.4 接管与人工输入

- 接管是行为变更，不再只是协调提示。
- 人工输入立即取得写权并视同接管，等待中的模型动作取消且不重放。
- 已开始 CDP 动作可能已产生副作用，不能宣称回滚。
- 用户显式交还后才允许新的模型写入。

### 4.5 共享写入

BrowserSession、Dashboard 原始输入、harness、record daemon 和 ReplayRunner 都使用 profile 级锁/lease。record daemon 已是第二进程，跨进程 lease 属于 P2，不得推迟到生产阶段。

当前候选实现已提供 OS 文件锁、`CDPClient` command gate 及 Octop BrowserSession 创建/重试注入，并验证两个进程的互斥与崩溃释放。launch/attach 和初始 WebSocket connect 也由外层 gate 覆盖；外层在 `enable_domain` 前释放，避免与每命令 gate 嵌套取锁。两个 spawn 进程执行真实 `BrowserSession.create` 时，以计时驱动桩验证 launch/connect 区间不重叠。record daemon、ReplayRunner 和跨进程接管状态尚未完成统一接入；不能把现有 gate 当作 AC-15、AC-40、AC-51、AC-60 已全部通过。

## 5. 技能生成、录制转换与两类执行

### 5.1 模型生成

复用 SDK 的 `GENERATE_SKILL_TOOL`、`SkillGenerator`、`GeneratedSkillDraft`、`SkillCandidateSink`。保留现有工具轨迹、frontmatter、输入与二进制校验，不新增宿主专用生成器。

### 5.2 人工录制

```text
octop-browser recorder
  → 脱敏原始事件
  → semantic / matcher
  → StepsDocument
  → 固定模板
  → GeneratedSkillDraft（不含 steps）
  → 客户端校验与用户确认
  → SkillCandidateSink
  → WebSkill Console/governance
```

- 人工路径省略 steps，不伪造轨迹或陈旧 ref。
- SKILL.md 由固定模板生成，不调用模型。
- Octop 负责录制素材转换；Console 负责预览、审查、发布、撤销和版本。
- 来源通过提交提示与 Octop 审计展示，不扩展 sink/draft 公共契约，不把 provenance 塞进 description。
- `ReplayRunner` 不消费该 draft；技能执行由 agent 读 SKILL.md 并重新感知。

### 5.3 候选跨端接口

`POST /api/browser-agent/sessions/{id}/skill-drafts` 返回：

```json
{
  "draft": {
    "name": "recorded-example",
    "description": "A template-generated skill from a reviewed recording.",
    "content": "---\nname: recorded-example\n---\n..."
  },
  "provenance": {
    "recordingId": "recording-id",
    "sourceDigest": "server-side-source-digest",
    "draftDigest": "client-verifiable-draft-digest",
    "evidence": "recording"
  }
}
```

- `sourceDigest` 覆盖原始录制材料，仅 Octop 审计和对账，客户端无法重算。
- `draftDigest` 使用固定字段白名单、递归键排序、无格式空白的 JSON 文本计算 SHA-256；数组顺序保留，字符串内容（包括其中空白）不变，接受有限数字，拒绝 undefined、非有限数值和其他非 JSON 值。两端须用同一测试向量验证，不能用同形状对象的重复 JSON.stringify 代替跨键序测试。
- `steps` 和其他未知字段必须显式拒绝，不能在规范化时静默丢弃；digest 与 `sink.submit` 必须使用同一个规范化结果。探针需覆盖有限数字、非有限数字和未知字段。
- 不把 provenance 加入 GeneratedSkillDraft；`recordingId` 不得借用 `sink.submit` 的 `sessionId`。后者只接收真实 WebSkill 会话；人工录制来源留在 Octop 审计与提交提示。
- 服务端校验登录、browser 权限、精确 Origin、Fetch Metadata、会话和 recording 归属。
- 客户端校验 draft schema、frontmatter、名称、体积、文件清单、会话归属及无 steps。
- 复用 SDK 公开 YAML/技能解析与载荷校验；不能 import 私有实现，缺入口时回需求评审。
- P0 确认 SDK 没有公开的 GeneratedSkillDraft 整体验证器。跨端不可信 envelope 的严格结构、字段白名单、无 steps、体积和归属校验由 Octop 适配层负责；frontmatter 使用公开 parseSkillMarkdown/isValidSkillName，最终仍经既有 sink/candidate 校验。无需为该宿主新增 SDK 公共 API。
- 人工路径不调用 SkillGenerator 冒充模型轨迹生成；现有 octop-browser Bash SKILL.md 只作为原始模板基线，必须经过固定 WebSkill 模板适配后才能进入候选链。候选 sink 接受该原文不构成 AC-22/AC-53～AC-55 通过。

### 5.4 录制回放

ReplayRunner 消费 `StepsDocument`，在 Octop 主进程运行。它接受注入的执行器；增强路径经过权限、工作集、接管和逐步 grant。record daemon 不自主回放，不持有 Octop 签名密钥，不新增 daemon 回调协议。

执行器注入是 octop-browser 接口变更；旧 CLI 可保留兼容行为，但增强路径不能使用默认 browser_tool 直连。

### 5.5 技能执行

技能执行由 agent 读取 SKILL.md，调用 `perceive_page` / `act_on_page`。`act_on_page` 只接受新鲜 ref，拒绝 selector。扩展宿主与增强版分别真实执行一次；不得用 ReplayRunner 冒充技能执行验收。

## 6. 录制隐私与生命周期

复用 `privacy.py`，补 Cookie、Authorization、API key、OTP、支付信息、PII、输入变量化与截图默认关闭。候选预览前再次检查脱敏。

初始建议每用户最多 50 份、默认保留 30 天，P0 评估后锁定。用户可删除原始步骤、截图、输入和回放报告；删除后不能回放或转换。已发布技能在 Console 单独处理，审计只保留无敏感原文的 tombstone，保持链完整。

## 7. 文档产物与 opaque viewer

- viewer 不可读 Dashboard storage、模型密钥或同源 API。
- 强制 opaque origin，不使用 allow-same-origin。
- sandbox 只能由 HTTP 响应头下发，不能用 meta。
- 固定 allow-scripts/allow-modals/allow-downloads；不允许宿主自行扩大。
- viewerCspHeader 和 sandbox token 保持单一来源。
- 静态模块资源处理 Origin:null 与 CORS，不关闭脚本 CSP。
- 使用真实 HTMLIFrameElement.contentWindow 与 source/channel 校验，不能用代理伪造握手。
- 内嵌 iframe 的 `ViewerShellHost.opener` 在宿主外壳绑定真实 `window.parent`；不得直接传 `window` 后依赖为 null 的 `opener`。该适配复用现有端口，不改变公共 WindowLike。
- 内嵌 iframe 的删除、监听器 dispose 和 viewer 会话回收由 Octop 宿主负责；不能依赖 `contentWindow.close()` 删除宿主拥有的 DOM。
- 若需修改 WindowLike/握手公共契约，先回需求与 API snapshot；无法可靠实现时本阶段不开放产物投放。

复用文档解析、模板、图片、链接文档、审计和下载端口。PPT/DOCX/XLSX/大屏需真实生成并打开验收，不能只检查技能列表。

## 8. UI、静态构建与同源信任

- Octop React 18 与 WebSkill React 19 分开打包；受信 Chatbot/Console 使用同源 iframe 隔离 React 运行时。
- 不直接编辑 `src/octop/dashboard/` 生成物。
- 独立 Vite 配置产出静态包，再由 Octop 构建流程复制。
- 固定 SDK/chatbot/console 和构建工具版本；哈希资源长期缓存，HTML 可更新。
- ThemeScope、语义 token、双语和实时 RuntimeAppearance 订阅不变。
- P1 选择受信同源宿主：没有不可信第三方脚本/插件，CSP 与依赖审计通过；出现不可信脚本时取消支持，不声称同源 iframe 隔离存储。

## 9. API、新旧接口与错误

### 9.1 内部接口

| 接口                                                 | 用途                        |
| ---------------------------------------------------- | --------------------------- |
| `POST /api/browser-agent/sessions`                   | 创建用户会话                |
| `GET /api/browser-agent/sessions/{id}`               | 查询能力与文档状态          |
| `DELETE /api/browser-agent/sessions/{id}`            | 释放会话，不杀共享 Chrome   |
| `POST /api/browser-agent/sessions/{id}/tickets`      | 实际连接前签发一次性 ticket |
| `POST /api/browser-agent/sessions/{id}/grants`       | 签发普通动作 grant          |
| `POST /api/browser-agent/sessions/{id}/skill-drafts` | 返回人工录制模板草稿        |
| `WS /api/browser-agent/ws?ticket=...`                | 受限 PageAgent 请求与事件   |

### 9.2 既有接口迁移

| 现有接口                         | 处理                                                   |
| -------------------------------- | ------------------------------------------------------ |
| `/browser-stream/ws`             | 改 ticket + viewTarget，画面和输入都钉住该 target      |
| `/browser/harness-sessions`      | 继续复用，不新建重复列表                               |
| `/browser/sessions/{id}/handoff` | 保留入口，升级为强制写拦截                             |
| `/browser/record-replay/*`       | 继续复用，主进程 ReplayRunner 注入授权执行器           |
| 原始 CDP / eval_js               | 不向 WebSkill 暴露；共享 profile 纳入 harness 权限与锁 |

P1 新页面与 PageAgent 均使用一次性 ticket。旧工作台的 `?token=<JWT>` 仅为兼容入口，日志不得记录原文，目标在 Octop `1.1.0` 移除；不形成无限期双轨。

### 9.3 错误

错误码白名单包含：

- `PERCEPTION_FAILED`
- `PERCEPTION_SUBTREE_UNSUPPORTED`
- `PERCEPTION_SUBTREE_OUT_OF_SCOPE`
- `PERCEPTION_SUBTREE_UNKNOWN_REF`
- `PAGE_ACTION_STALE_REF`
- `PAGE_ACTION_OUT_OF_SCOPE`
- `PAGE_TAB_UNKNOWN`
- `PAGE_TAB_EXPIRED`
- `TOOL_EXECUTION_FAILED`

未知码降级为 `TOOL_EXECUTION_FAILED`，不把字符串直接断言为 WebSkillErrorCode。感知与操作分别使用对应语义，空结果不能冒充成功。运行时与 daemon 输出使用英文，UI 双语展示。

## 10. 资源、观测与部署

- 不可见工作区暂停抓图；同一 profile 单采集多订阅。
- 不每会话启动 Chrome；限制 Chrome、target、frame、连接和队列。
- 后台 viewTarget 连续 60 秒出帧需实测；失败时显示暂停/切前台提示，不自动切页。
- requestId 贯通客户端、HTTP/WS、Octop 和 octop-browser。
- 指标包括成功率、错误码、p50/p95、锁等待、target/CDP 连接/订阅数。
- 动作、grant、投放、候选、技能执行和录制回放进入可验证审计链。
- 日志不含完整 prompt、模型密钥、Cookie、敏感输入或页面敏感正文。

## 11. 上游与发布

- 小 PR 上游优先，不维护长期私有 fork。
- 发布顺序：octop-browser → octop-harness → Octop。
- ReplayRunner 执行器注入是该库的显式接口改动，需要版本、行为测试和兼容说明。
- octop-browser 正式发布需要可核对的上游 tag/发布记录。
- SDK 公共契约变更先过需求和 API snapshot。
- 已撤回 npm 版本永不复用；回滚优先 dist-tag/deprecation。
- 发布检查继续通过 gh 手动触发，不恢复 push 自动发布。
- 真实安装包与运行环境必须和兼容矩阵一致。
