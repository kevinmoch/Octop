# Octop 服务端增强版需求基线

> 状态：🟢 P0/P1/P2 本地候选已完成；🟢 P3 本地 candidate；🟢 R0 能力基线已建立；🟢 R1 原生技能学习本地 candidate 通过（真实 qwen3.5:9b）。
> 本专项不规划 WebSkill 的 npm 新版本；P0/P1/P2/P3 均为集成阶段。
> 阶段与实施边界见 [00-index.md](00-index.md)，技术设计见 [design/00-overview.md](design/00-overview.md) 与 [R0 能力基线](design/01-capability-baseline.md)，验证见 [P0 探针](verification/00-p0-probes.md) 与 [P1 实施及真实验证记录](verification/01-p1-acceptance.md)。

## 1. 目标

在 Octop Dashboard 内提供无需专用扩展的服务端浏览器增强能力：真实 Chromium 负责页面，WebSkill 负责 Agent、技能、Chatbot、Console、独立模型和 Token。主文档之外还需覆盖 frame、OOPIF、任务新窗口、写入协调、录制回放、技能执行和文档产物。

## 2. 非目标

- 不恢复旧 WebSocket 控制器网关或 Octop 专用 iframe 扩展。
- 不删除目标网站安全响应头，不提供通用网页代理。
- 不合并 Octop 与 WebSkill 的模型、Token、技能、专家、连接器或 MCP 账本。
- 不向 WebSkill 前端暴露任意 CDP、任意 JS、任意 profile 或任意文件路径。
- 不承诺验证码、反自动化、原生系统对话框和所有浏览器特权能力。
- 不把动态网页的模型执行描述成确定性逐步回放。

## 2.4 R0 能力整改决策（2026-11-19）

本节只增加整改范围与优先级，不改变既有 FR/AC 的通过状态。每个能力的状态以 [capability-inventory.json](capability-inventory.json) 和对应证据为准。

- **原生技能学习是主路径**：真实会话 `ToolStep` → `generate_skill` → `SkillGenerator` → `CandidateStore` → Console 审核发布；固定模板录制候选和录制回放只能是 Octop 辅助，不得作为原生学习证据。
- **能力按端口逐项验收**：主页面、pageTabs、分页、PDF、pageImage、downloads 等分别有状态；remoteImage、WebOffice、DWG、页面声明技能默认 `unsupported`，不得以“完整能力”名义开放任意 URL、requestkey、路径或脚本。
- **R2 拆分**：R2A 页面对等、R2B 安全资源端口、R2C 高风险探针/不支持；R1 原生技能先于 R2。
- **公共 SDK 端口不足时按不支持处理**：不在 Octop 私造协议；如必须扩展，另立 SDK 需求、API snapshot 和版本评审，不夹带发版。
- **真实外部模型不可替代**：R1 已用本地 `qwen3.5:9b` 完成真实闭环（本地 candidate）；脚本化模型只用于回归，不补绿。
- **两宿主同场景**：每个能力至少按 [r0-two-host-scenarios.json](r0-two-host-scenarios.json) 记录 extension / octop-server 的输入、授权、结果和失败；不以截图像素代替语义对等。

## 3. FR 基线与阶段映射

| FR    | 需求                                                         | 阶段    | 关联 AC                                         |
| ----- | ------------------------------------------------------------ | ------- | ----------------------------------------------- |
| FR-01 | 无专用扩展的宿主、权限、安全上下文和静态包                   | P1 / P4 | AC-16、AC-17、AC-20、AC-31、AC-35、AC-50        |
| FR-02 | 主文档、frame、OOPIF、文档身份与 ref                         | P1 / P2 | AC-01～AC-08、AC-37、AC-47                      |
| FR-03 | 新 target 工作集、连续操作和双指针                           | P2      | AC-09～AC-13、AC-36、AC-42、AC-43、AC-45        |
| FR-04 | 确认、grant、接管、写入互斥和断线                            | P1 / P2 | AC-14、AC-15、AC-18、AC-19、AC-40、AC-41、AC-51 |
| FR-05 | 模型生成、人工录制、候选转换、脱敏、录制回放和技能执行       | P3      | AC-21～AC-24、AC-46、AC-52～AC-58               |
| FR-06 | PPT、DOCX、XLSX、大屏与 opaque viewer                        | P3      | AC-25～AC-28                                    |
| FR-07 | Chatbot/Console、独立模型、Token 和中英文主题                | P1 / P3 | AC-29、AC-30、AC-31、AC-35、AC-49、AC-50        |
| FR-08 | 资源、指标、重启恢复、部署和容量                             | P2 / P4 | AC-32～AC-34、AC-44、AC-45                      |
| FR-09 | 错误白名单、兼容矩阵、共享 profile 的 eval_js 和公共契约纪律 | P0～P4  | AC-38、AC-39、AC-48、AC-59、AC-60               |

## 4. 核心功能要求

### 4.1 安全上下文

- 远程访问必须使用 HTTPS；localhost 可例外。
- 在挂载 WebSkill UI 前检查 `isSecureContext`、OPFS 和 WebCrypto。
- 普通远程 HTTP 下必须显式拒绝并显示中英文可操作错误。
- 不允许以内存 FS 静默替代 OPFS，或让运行到一半的写操作静默丢失审计。
- 模型直连的 HTTPS/CORS/本地网络访问权限（LNA）失败必须显式报告，不能回退 mock/Octop 模型。新版 Chromium 使用 `local-network` / `loopback-network` 权限，不把旧 PNA 请求头当成准入依据；权限查询成功不等于拒绝路径已验证，LNA 拒绝场景须在支持的浏览器中单独实测。
- 明确无鉴权的本地模型不得要求用户填写 apiKey。P1 由宿主在内部提供非秘密兼容占位值，只对显式标记无鉴权的 provider 生效；不要求用户输入、不保存或展示为凭据，不进入日志或审计原文，并确认端点接受该请求格式；不修改或发布 SDK。原生空 key 支持后移除占位逻辑；需要真实鉴权的服务不得使用该占位路径，P1 必须验证真实鉴权端点使用占位值时鉴权失败而非静默放行。

### 4.2 页面感知与操作

- 顶层文档 key 是 registry 的唯一分片键，包含 target、loader 和当前 URL 的真实变化。
- 以 `backendNodeId` 维持节点身份，避免 DOM 插入后 ref 被新节点抢走。
- 子 frame 独立导航后旧 ref 必须失败；不使用坐标或同名元素兜底。
- 感知越权使用 `PERCEPTION_SUBTREE_OUT_OF_SCOPE`，操作越权使用 `PAGE_ACTION_OUT_OF_SCOPE`。
- 未授权 frame 使用现有 FrameNote 单一说明路径，不能额外生成重复说明节点。
- 选择器必须限长、限帧、限节点、限时，并使用成熟 CSS 解析依赖。

### 4.3 双指针与新 target

- `viewTarget` 同时决定画面和人工输入的目标。
- `modelTarget` 决定后续模型感知和操作的目标。
- 用户切换标签只改变 `viewTarget`，不改变 `modelTarget`，除非用户显式跟随/设为助手目标。
- 只有当前任务动作打开的新 target 才能自动进入工作集；用户手工标签不自动扩大权限。
- `envelope.targetId` 只能来自适配层维护的 `modelTarget`；伪造值必须拒绝或忽略并审计。
- 后台 viewTarget 的出帧能力必须实测；失败时明确暂停/提示，不自动切走用户画面。

### 4.4 授权、接管与并发

- 普通动作 grant：客户端确认后申请，绑定用户、会话、文档、target、frame、ref、动作和完整载荷摘要。
- 回放步 grant：Octop 服务端在有效回放范围内签发，只证明步骤仍在有效回放授权内，不证明用户逐步同意。
- 两种 grant 都一次性、短期、可核销，重放、换值、跨文档、跨 frame、跨用户均拒绝。
- grant TTL 从用户确认或回放步骤获得执行资格时计算，不从确认卡展示时起算。
- 接管从提示升级为强制写拦截，是明确行为变更。
- 任何人工输入立即取得写权并视同接管；等待中的模型动作取消且不重放，已开始动作不宣称回滚。
- 所有写路径使用 profile 级共享锁/lease，覆盖 Dashboard、harness、record daemon 和 ReplayRunner。

### 4.5 技能候选、人工录制与模板

- **原生技能学习是主路径**：真实会话工具轨迹 → `generate_skill` → `SkillGenerator` → 候选 sink；R1 本地 candidate 已验证“生成 → Console 发布 → 新会话执行”，固定模板人工录制只能作为 Octop 辅助，不得冒充该路径。
- 模型会话生成技能复用 `SkillGenerator` / `GENERATE_SKILL_TOOL` 和 `SkillCandidateSink`。
- 人工录制使用 octop-browser 的 `StepsDocument`，经固定模板转换为 `GeneratedSkillDraft`。
- 人工录制草稿省略 `steps`；不伪造本会话工具轨迹，不写跨会话陈旧 ref。
- 人工录制的 SKILL.md 不调用模型，模板内容固定、可审查、可验证。
- Console/governance 负责候选预览、审查、发布、撤销和版本管理。
- Console 来源展示不扩展公共 sink 契约：使用候选提交时的来源提示 + Octop 审计，不把 provenance 塞进 description 或公共 draft 字段。
- `sourceDigest` 仅用于 Octop 审计和对账；客户端不得声称能重算原始录制摘要。

### 4.6 候选跨端与摘要校验

- Octop 通过受限 `POST /api/browser-agent/sessions/{sessionId}/skill-drafts` 返回 `{draft, provenance}`。
- 该接口需要登录、browser 权限、精确 Origin、Fetch Metadata 校验和 recording 归属校验。
- 客户端将服务端 draft 视为不可信输入，校验 schema、frontmatter、体积、文件清单、会话归属和人工路径无 steps。
- `draftDigest` 覆盖规范化 draft JSON，客户端可重算并校验。
- `sourceDigest` 只用于 Octop 审计，客户端只转发/保留来源信息，不做原始材料摘要校验。
- 用户确认后才调用 `sink.submit({draft, sessionId, confirmedAt})`。
- 人工路径不直接调用 `SkillGenerator` 冒充模型轨迹生成；可复用 SDK 公开 YAML/技能解析、名称和载荷校验。缺少公开校验入口时必须回到需求评审，不 import 私有实现。

### 4.7 录制回放与技能执行分离

- 录制回放由 `ReplayRunner` 消费 `StepsDocument`，只在服务端提供。
- 技能执行由 agent 读取 SKILL.md 并调用现有工具，两种宿主都需真实验收。
- `ReplayRunner` 增加可注入执行器是 octop-browser 的接口变更，需版本、行为测试和兼容说明。
- 增强路径必须注入授权执行端口，不得使用默认直连 `browser_tool`。
- 回放由 Octop 主进程编排，daemon 只录制，不持有 Octop 签名密钥。
- 一次回放级用户授权绑定 recordingId、起始 target、范围、最大步骤数和有效期；每步仍获取独立 grant。
- 回放会话在服务端保存，崩溃/重启/接管/取消后失效，不自动恢复。
- 已开始动作不宣称回滚；停止意味着不再执行后续步骤。

### 4.8 录制数据生命周期

- 录制和回放报告有保留期限与容量上限；固定默认为 30 天、每用户 50 份（含进行中的录制），由 octop-browser 的录制设置统一管理。
- 用户可删除原始步骤、截图、输入变量和回放报告；删除后不能继续回放或转换候选。
- 已发布技能不因删除录制自动删除，需在 Console 单独处理。
- 审计只保留不含敏感原文的 tombstone 和来源标识，保持审计链完整。
- 脱敏覆盖 Cookie、Authorization、API key、OTP、支付信息、PII、输入变量和截图策略。

### 4.9 产物和 UI

- 文档 viewer 必须 opaque origin，HTTP 响应头下发 sandbox，禁止 `allow-same-origin`。
- 内部 viewer 使用真实 `contentWindow` 和 source/channel 校验；不能用代理或类型断言伪造握手。
- 需要改变公共 `WindowLike`/握手契约时先修改需求并验证 API snapshot。
- 复用 DOCX/XLSX/PPTX/PDF、模板、图像、链接文档、下载和审计端口；扩展特权能力须明确替代或不支持。
- P1 使用受信同源宿主，同源 iframe 只隔离 React 运行时，不隔离存储；不可信同源脚本出现时组合不再受支持。
- Chatbot/Console 不因 Octop 宿主而 fork；语言、主题和权限使用现有订阅和端口。

## 5. 兼容矩阵与部署准入

| Octop tag / commit | Harness            | Browser            | SDK        | Chatbot    | Console    | Chromium                        | 部署形态 / 持久化         | 状态与证据                                     |
| ------------------ | ------------------ | ------------------ | ---------- | ---------- | ---------- | ------------------------------- | ------------------------- | ---------------------------------------------- |
| `a01c397e`         | `1.0.9 / 65b54561` | `b3910fc5 / 1.0.0` | `0.29.0`   | `0.26.0`   | `0.22.5`   | `157.0.8103.79 / 149.0.7827.55` | 单进程 / 本地临时 profile | `candidate`；P1 已验收，未支持生产          |
| `ff078d3f` / `1.0.3b6` | `1.0.10` | `b3910fc5 / 1.0.0` | `0.29.0` | `0.26.0` | `0.22.5` | `157.0.8103.79 / 149.0.7827.55` | 单进程 / 本地临时 profile | `candidate`；上游同步后 P1 复验，未支持生产 |
| `P2-10 本地冻结候选` / `1.0.3b6` | `1.0.10` | `42d6f7c` / `1.1.0+webskill.local.1` | `0.29.0` | `0.26.0` | `0.22.5` | `156.0.8078.79` | 单副本 / PVC | `candidate`；真实 Pod 重建与登录态保留已验收，wheel SHA-256 已 pin；普通受信 TLS 与最终冻结回归待收尾 |
| `P2-10 本地冻结候选` / `1.0.3b6` | `1.0.10` | `42d6f7c` / `1.1.0+webskill.local.1` | `0.29.0` | `0.26.0` | `0.22.5` | `156.0.8078.79` | 多副本 / `emptyDir` + Ingress 亲和 | `candidate`；真实亲和、旧会话失效和重新登录已验收；不支持跨 Pod 共享写入或登录态恢复 |

- 状态只能为 `candidate`、`supported`、`blocked`、`deprecated`。
- 新版 Octop 先进入 candidate，完成真实安装和 FR/AC 验收后才可 supported。
- 未列入或 blocked 的组合拒绝启用，显示中英文修复说明。
- 不自动退回 latest，不任意使用更高版本，不放宽未实测 peer range。
- 矩阵需同步为机器可读配置，记录部署形态与 profile 持久化。
- 多副本必须复用现有 Ingress 会话亲和；文件锁不能覆盖跨 Pod。
- `emptyDir` 仅提供 Pod 生命周期内持久化；Pod 重建后明确报会话丢失，不自动复制旧 profile 或假装登录态存在。

## 6. 验收环境标记

`unit`、`real-browser`、`real-service`、`two-user`、`network`、`https`、`harness`、`daemon`、`console`、`k8s`、`ci`、`extension`、`security-review`。真实组件不能用 mock 替代；`security-review` 是人工检查项。

## 7. AC 验收基线

| ID    | 场景                      | 环境                                          | 通过标准                                                                                                      |
| ----- | ------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| AC-01 | 主文档感知与一次点击      | real-browser + real-service                   | 目标只变化一次，无重复执行                                                                                    |
| AC-02 | DOM 插入后使用旧 ref      | real-browser                                  | 原 ref 仍指向原节点，不误点新节点                                                                             |
| AC-03 | 导航后旧 ref/grant        | real-browser + real-service                   | 明确过期，无坐标兜底                                                                                          |
| AC-04 | 同源 frame 读取/点击      | real-browser                                  | 内容、身份、授权和副作用正确                                                                                  |
| AC-05 | 跨源 OOPIF 读取/操作      | real-browser                                  | 正确执行上下文；不支持时明确失败                                                                              |
| AC-06 | 子 frame 独立导航         | real-browser                                  | 旧 frame ref 失效，不误用主文档 ref                                                                           |
| AC-07 | 显式未授权感知/操作       | unit + real-service                           | 分别使用 PERCEPTION_SUBTREE_OUT_OF_SCOPE / PAGE_ACTION_OUT_OF_SCOPE                                           |
| AC-08 | 多 frame 聚合             | unit + real-browser                           | 客户端 frameRouter 聚合，一个顶层文档 key，预算生效                                                           |
| AC-09 | target=\_blank            | real-browser + real-service                   | 进入内部标签和任务工作集                                                                                      |
| AC-10 | window.open 后继续        | real-browser + real-service                   | modelTarget 正确切换，副作用只一次                                                                            |
| AC-11 | 双指针分离                | real-browser + real-service                   | 画面/输入保持 viewTarget，模型操作 modelTarget                                                                |
| AC-12 | 用户切换标签              | real-browser + real-service                   | viewTarget 改变，modelTarget 不变，不扩大授权                                                                 |
| AC-13 | 关闭 target               | real-browser + real-service                   | ref/grant/指针正确失效，不擅自落到其他 target                                                                 |
| AC-14 | 用户接管                  | real-service + real-browser                   | 新 grant 和所有相关写路径被拒绝                                                                               |
| AC-15 | 并发写                    | real-service + harness + daemon               | 同时刻最多一个写入者；等待者明确状态；页面副作用只一次                                                        |
| AC-16 | 无 browser 权限           | real-service                                  | 会话、读取和写入均拒绝                                                                                        |
| AC-17 | 跨用户猜测 ID             | two-user                                      | A 无法读取/操作 B 的页面                                                                                      |
| AC-18 | WS 断线重连               | real-service                                  | 在途请求失败，写操作不自动重放                                                                                |
| AC-19 | grant 载荷/时序/Origin    | unit + real-service                           | 换值、重放、过期、错误 Origin 均拒绝                                                                          |
| AC-20 | 既有 JWT query 迁移       | real-service                                  | 最终画面流不携带长期 JWT                                                                                      |
| AC-21 | 录制与脱敏                | real-browser + daemon                         | 使用既有 recorder，无密码、Token、Cookie、OTP、PII 泄漏                                                       |
| AC-22 | 录制转换候选              | real-service + console                        | 有效 GeneratedSkillDraft 被 sink 接受，Console 可预览                                                         |
| AC-23 | 录制回放授权              | real-service + real-browser                   | 主进程编排，授权执行器生效，不在 daemon 直绕 browser_tool                                                     |
| AC-24 | 动态页面回放失败          | real-browser                                  | 目标不明确或变化时停止，不随机点击                                                                            |
| AC-25 | PPTX/DOCX/XLSX            | real-browser + console                        | 文件有效、内容正确、可实际打开                                                                                |
| AC-26 | 大屏 HTML                 | real-browser                                  | 中文、主题、资源和交互正确                                                                                    |
| AC-27 | opaque viewer 隔离        | real-browser + real-service                   | 无 Dashboard storage/密钥访问；HTTP sandbox 与 CSP 生效                                                       |
| AC-28 | 内部 viewer 握手          | real-browser                                  | 真实 contentWindow、source/channel 校验、关闭清理正确                                                         |
| AC-29 | 模型和 Token 隔离         | real-service                                  | 使用 WebSkill 配置与计量，不进入 Octop 模型/账本；占位值仅限显式无鉴权 provider，真实鉴权端点不得被其静默放行 |
| AC-30 | 中英文与明暗主题          | real-browser                                  | 实时生效，无错误文案或白斑                                                                                    |
| AC-31 | 静态包更新                | real-service                                  | React 不混包，HTML/资源版本一致                                                                               |
| AC-32 | 隐藏、多观看端、空闲      | real-service                                  | 不重复抓图，容量和回收策略生效                                                                                |
| AC-33 | 服务重启/Chrome 崩溃      | real-service                                  | 旧会话失效，可明确重建，无猜测恢复                                                                            |
| AC-34 | 性能与指标                | real-service + network                        | 达成经 P0 校准的 p95，指标无敏感内容                                                                          |
| AC-35 | 非安全上下文              | network + https + real-browser + real-service | HTTP 显式拒绝，HTTPS/localhost 可运行，不静默丢数据                                                           |
| AC-36 | 工作集触顶                | unit + real-service                           | 返回可诊断原因，原动作状态不伪造                                                                              |
| AC-37 | 选择器超长/超预算         | unit + real-service                           | 明确拒绝并记录，不超时、不静默截断                                                                            |
| AC-38 | 未知错误码                | unit                                          | 降级为 TOOL_EXECUTION_FAILED，无非法类型断言                                                                  |
| AC-39 | 未列/blocked 组合         | real-service                                  | 拒绝启用并提供中英文修复说明                                                                                  |
| AC-40 | lease 超时/进程崩溃       | real-service + daemon                         | fail-closed，无第二写入者，无遗留 grant                                                                       |
| AC-41 | 审计链完整                | real-service + console                        | 动作、grant、投放、候选、技能执行和回放可追踪；断链/坏行显式报告                                              |
| AC-42 | 视图内输入                | real-browser + real-service                   | 鼠标、键盘、滚动、导航只发往 viewTarget                                                                       |
| AC-43 | 伪造 envelope.targetId    | unit + real-service                           | 拒绝或忽略并审计，不改变 modelTarget                                                                          |
| AC-44 | 亲和失效/Pod 重建         | real-service + k8s + real-browser             | 明确会话丢失与重新登录要求，不自动复制旧 profile                                                              |
| AC-45 | 后台 viewTarget 连续出帧  | real-browser                                  | 连续 60 秒有真实帧；失败明确暂停/提示，不伪造实时画面                                                         |
| AC-46 | 模型会话生成技能          | real-browser + console                        | 复用 SDK 生成链，轨迹和草稿一致，可审核发布                                                                   |
| AC-47 | 未授权 frame 说明         | unit + real-browser                           | 现有 FrameNote 单一路径渲染，不重复节点，审计有记录                                                           |
| AC-48 | 公共契约与版本纪律        | unit + ci                                     | 未批准 API snapshot diff 为 0；撤回版本不复用                                                                 |
| AC-49 | 模型 HTTPS/CORS/LNA       | https + network + real-browser                | 直连失败给出可操作错误，不回退 mock/Octop 模型                                                                |
| AC-50 | 受信同源宿主前提          | security-review + real-service                | 无不可信同源脚本；CSP 与依赖审计通过；引入不可信脚本即取消支持                                                |
| AC-51 | 人工输入优先              | real-browser + real-service                   | 人工输入立即取得写权并视同接管；等待中的模型动作取消且不重放                                                  |
| AC-52 | 发布后的技能执行          | extension + real-browser + real-service       | 两宿主各由 agent 执行一次，结果语义一致、授权不绕过，不调用 ReplayRunner                                      |
| AC-53 | 人工录制 steps 策略       | unit + console                                | 省略 steps，不含陈旧 ref/虚构轨迹；提交提示与 Octop 审计可证明来源，不要求 Console 新增字段                   |
| AC-54 | draft 跨端与摘要          | real-service + console                        | draftDigest 由客户端重算通过；sourceDigest 仅入 Octop 审计；来源、体积和归属校验通过                          |
| AC-55 | 模板生成 SKILL.md         | unit                                          | 不调用模型；相同脱敏输入输出一致；内容通过技能解析与人工审查                                                  |
| AC-56 | 录制数据生命周期          | real-service + console                        | 保留上限生效；删除后不能回放/转换；tombstone 不含敏感内容                                                     |
| AC-57 | ReplayRunner 执行器注入   | unit + real-service                           | octop-browser 接口变更经测试；增强路径不直接调用默认 browser_tool                                             |
| AC-58 | 回放授权粒度              | real-service + real-browser                   | 一次范围授权；每步服务端 grant；接管/取消/过期后不执行后续步骤                                                |
| AC-59 | 首个 candidate 组合       | real-service                                  | 可进入 P0 但不误标 supported；未知组合仍拒绝                                                                  |
| AC-60 | eval_js 共享 profile 策略 | harness + real-service                        | WebSkill 无此入口；harness 权限、锁和接管生效，无绕过                                                         |

## 8. 依赖与发布边界

- 本专项不包含 WebSkill SDK、Chatbot、Console 或 polyfill 的发版计划，也不分配新版本号。
- 若发现通用 SDK 缺陷，必须在所属包另行进行需求和版本评审；不得借 Octop 集成名义夹带发布。
- Octop 及其运行库的依赖顺序：octop-browser → octop-harness → Octop。
- 公共契约变化先改需求并通过 API snapshot/兼容性评审。
- 若未来独立发布 WebSkill 包，遵守 WebSkill SDK 所属仓库的 `RELEASING.md`，包括撤回版本不可复用与 gh 手动触发；这些不是本专项的发版步骤。
- 真实安装包和实际运行环境必须与兼容矩阵一致。
