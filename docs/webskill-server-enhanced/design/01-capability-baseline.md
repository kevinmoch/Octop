# R0 能力基线与合同

> 状态：🟢 R0 技术基线已建立；🟢 R1 原生技能学习本地 candidate 通过；不是生产、不是完整能力声明。
> 基线：Octop `facb499b3`、browser fork `1.1.0+webskill.local.2`（wheel SHA-256 `b66182b2…`）、SDK `0.29.0` / Chatbot `0.26.0` / Console `0.22.5`。
> 机器可读事实源：[../capability-inventory.json](../capability-inventory.json)。
> 两宿主场景合同：[../r0-two-host-scenarios.json](../r0-two-host-scenarios.json)。

## 1. R0 目标

R0 不实现新能力，只把“完整 WebSkill 保留”拆成可核验的合同：

1. 每个公开宿主端口有 SDK 表面、Octop 装配、UI 入口、状态、决策、阶段和证据。
2. 明确“原生技能学习”与“Octop 录制辅助/回放”不是同一条链。
3. 明确高风险资源端口默认不支持，不作为“完整能力”的隐藏承诺。
4. 建立两宿主（extension / octop-server）同输入、同语义、同授权规则的场景合同。
5. 把 UI 上的能力宣传改为真实状态，未接的端口显示“计划中”或“不支持”。

R0 不修改既有 FR/AC 的通过状态；新增/调整需求必须按需求变更流程评审，不能在本文里把未完成项写成通过。

## 2. 当前能力状态（摘要）

完整状态以 JSON 为准。摘要：

| 能力 | 当前状态 | R0 决策 | 阶段 |
|---|---|---|---|
| 主文档感知/动作、frame/OOPIF、嵌套 frame | candidate，有真实证据 | 保留 | R0 |
| 多 target 工作集/双指针 | candidate | 保留，R2A 完整矩阵 | R2A |
| 普通动作 grant、回放 step grant | candidate | 保留 | R0 |
| 接管写拦截/profile lease | candidate；多 worker 仍待硬化 | 保留 | R4 |
| 录制转固定模板候选 | candidate；不是原生学习 | 保留为 Octop 辅助 | R0 |
| 受限回放/逐步 grant | candidate | 保留为可选辅助 | R0 |
| 录制生命周期 30 天/50 份 | candidate | 保留 | R0 |
| DOCX/XLSX/PPTX/图片读取/模板 | candidate | 保留 | R3 |
| 文档产物、大屏、opaque viewer | candidate；原生 PDF 未通过 | 保留 | R3 |
| Console 治理/候选 | candidate | 保留 | R1 |
| 原生技能生成 `ToolStep → generate_skill` | **candidate：R1 真实外部模型本地闭环通过** | 保留 | R1 |
| 已发布技能跨会话执行 | candidate（R1 新会话重新读取/执行） | 保留 | R1 |
| `pageTabs` | candidate（R2A 真实 UI+模型通过） | 保留 | R2A |
| `pagePerceptionPaging` | candidate（segment 1→cursor→segment 2 真实通过） | 保留 | R2A |
| PDF 直传/逐页 | partial（pypdf 文本回退；逐页/渲染待补） | 实现 | R2B |
| `pageImage` | candidate（真实 UI+模型主文档取像） | 保留 | R2B |
| `downloads` | partial（会话级实现；真实下载验收待补） | 实现（仅当前会话已完成项） | R2B |
| `remoteImage` | unsupported | 默认不支持；独立 SSRF 评审后另议 | R2C |
| WebOffice / 原件模板 | unsupported | 默认不支持；不暴露 requestkey | R2C |
| DWG | unsupported | 默认不支持；独立 Worker/安全评审后另议 | R2C |
| 页面声明技能/WebMCP | unsupported | 不支持；不与 Octop 连接器合并 | R2C |
| 远程 MCP | not-applicable | WebSkill 自有 | R2C |
| 普通受信 TLS UI | blocked | 独立 R4 准入 | R4 |
| Docker 新 wheel 构建 | blocked | 独立 R4 准入 | R4 |
| 能力状态 UI | partial | R0 简版投影已接 | R0 |

## 3. 分层与阶段的硬边界

### Tier 0：核心页面对等

- 主文档、同源 frame、OOPIF、嵌套 frame；
- `viewTarget/modelTarget`、target 工作集、接管；
- `pageTabs`、`pagePerceptionPaging`；
- 所有动作仍受 grant、origin、target、文档、ref 和 secret 字段约束。

### Tier 1：原生技能学习（先于 Tier 2）

- `RuntimeConfig.agentCapabilities.skillGeneration` 默认关闭；
- 用户显式开启后，Chatbot 持久化脱敏 `ToolStep`；
- 模型调用 `generate_skill`，`SkillGenerator` 校验轨迹/附件/确认；
- 候选入 `CandidateStore` / Console 治理；
- 会话 B 读取已发布技能并重新感知执行；
- **不得**用固定模板候选、脚本化模型或 ReplayRunner 冒充 R1 通过。

### Tier 2：安全资源端口

- PDF、pageImage、downloads；
- 统一 opaque handle；
- 不暴露路径、任意 URL、原始 grant、requestkey；
- 每个端口有字节/像素/MIME/超时/过期/跨用户/跨 target 证据。

### Tier 3：默认不支持

- remoteImage、WebOffice、WebOfficeTemplate、DWG、页面声明技能/WebMCP；
- 只在独立安全/格式评审后另立项目；
- UI 不得保留必然失败的假入口；
- 不因“完整能力”名义打开任意网络、任意脚本或任意文件。

## 4. 端口合同

所有资源端口遵守：

```text
ResourceHandle
  id: random, opaque, unguessable
  user/profile/session
  kind
  target/frame/document (where applicable)
  created_at / expires_at
  size / mime / status
  audit event id
```

- 列表到读取之间重新验证归属/存在/授权/过期；
- 无服务器路径、无 requestkey、无任意 URL；
- 不跨 user/profile/session/target/document；
- 页面关闭、target 关闭、会话删除、录制删除、Chrome 崩溃后失效；
- 字节/像素/条目/页数有上限；超时/取消/错误码明确；
- 审计不记录 secret、完整 URL、服务器路径和页面敏感全文。

### 4.1 pageTabs

- SDK `TabWorksetPort` 的不透明 handle；
- focus 只改 `modelTarget`，不改 `viewTarget`；
- close 后 handle 失效；
- 新窗口只在该任务动作观察窗口内自动纳入 workset。

### 4.2 pagePerceptionPaging

- 使用 SDK 现有预算/续读；不私造协议；
- 不静默截断；明确 continuation/truncated；
- 取消/超时/预算耗尽可诊断。

### 4.3 PDF/pageImage/downloads

- PDF：受控字节通道，客户端解析；不暴露路径；
- pageImage：target/frame/ref 归属、clip、像素/字节/超时；
- downloads：仅当前会话已完成项，不读取用户本机既有下载，不读任意服务器文件。

## 5. 两宿主场景合同

见 [../r0-two-host-scenarios.json](../r0-two-host-scenarios.json)。每个场景记录：

- host、phase、fixture、inputs、authorization、modelKind、expected；
- 成功与失败都记录；
- 外部模型/脚本化模型分栏；
- 同输入同语义，不比截图；
- artifact 带 commit、wheel hash、环境。

R1 的 `native-skill-generate-reuse` 已用 `modelKind=external` 通过本地 candidate；R4 的 `trusted-tls`、`docker-wheel` 仍为 blocked，不能由 P3 viewer/文档证据替代。

## 6. 威胁模型

| 边界/威胁 | 控制 |
|---|---|
| 页面数据进入模型 | UI 明示；用户配置模型；secret 字段/输入/URL 脱敏 |
| 跨用户/会话/target | 每次请求校验 user/profile/session/target/document |
| 资源路径/读取 | opaque handle；无路径；重验证；过期；审计 |
| 远程 URL/SSRF | 默认不抓取；不开放代理；如做独立评审 |
| PDF/图片炸弹 | 字节/页/像素/超时/类型限制；临时文件 0600 |
| WebOffice requestkey | 不暴露；默认不支持 |
| DWG 解析器 | 默认不支持；Worker/超时/崩溃隔离另行评审 |
| 页面声明技能 | 默认不支持；不合并连接器 |
| viewer 逃逸 | opaque origin；HTTP sandbox；最小 CSP；无 allow-same-origin |
| 多 worker/lease | 进程内缓存/owner 状态需 R4 硬化；不夸大跨 Pod 锁 |
| 模型 LNA/TLS | 失败显式；不 mock/不降级；受信 TLS 独立 R4 |
| 将局部/脚本证据当完整验收 | 支持矩阵 + 两宿主 + external/scripted 分栏 |

## 7. R0 退出检查

- [x] 基线固定（Octop `facb499b3`、browser wheel SHA）。
- [x] 机器可读能力清单 `capability-inventory.json`。
- [x] 清单校验脚本 + 单测（host wiring 与状态一致）。
- [x] 两宿主场景合同 `r0-two-host-scenarios.json`。
- [x] 简版 UI 能力状态投影（`capabilityStatus.ts`）。
- [x] 需求/设计/索引增加 R0 决策和链接（见对应文档）。
- [x] R1 原生技能真实外部模型本地 candidate 闭环（`qwen3.5:9b`；见 04）。
- [ ] R2A/R2B/R2C 端口实现/探针（后续阶段）。
- [ ] R4 TLS/Docker/K8s/发布准入。

> R0 技术基线就绪不等于 R2/R3/R4 通过；R1 已单独用真实模型验收。任何新增能力必须更新本矩阵、场景合同和证据后才改变状态。

## 8. 下一阶段入口

R1 已完成本地 candidate（真实 `qwen3.5:9b`）。R2A 进入条件：

- R1 证据与 R0 矩阵评审确认；
- `pageTabs`、`pagePerceptionPaging`、target/frame 矩阵的端口合同和安全边界确认；
- 固定模板候选、录制回放不得作为 R2A 的证据；
- R2B/R2C 按 R0 决策和独立安全评审进入；R2C 默认不支持。
