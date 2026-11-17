# Octop 服务端增强版 · P0 技术探针与验证记录规则

> 状态：🟢 P0 本轮探针与评审整改已完成；这不表示完整 FR/AC 或生产组合已通过。
> 本文规定探针命令、环境和证据要求；探针输出只保存在本地 `.artifacts/webskill-p0/`，按仓库约定不将每轮结果入库。
> 需求见 [../00-requirements-overview.md](../00-requirements-overview.md)，设计见 [../design/00-overview.md](../design/00-overview.md)。

## 1. 原则

- 使用真实 Chromium、Octop 和 octop-browser，不以 mock 代替真实组件。
- 使用独立临时 profile、独立测试站点和独立端口，不读写真实 Octop 用户数据。
- 探针可证明“不支持/需要改造”，但不能为了通过而修改生产实现或伪造结果。
- 模型不使用 mock 代替真实本地模型；不可用时应明确报告。
- 不把 API snapshot、源码 import 或构建成功当成真实页面验收。

## 2. 基线证据

| 项目                 | 记录                                                               |
| -------------------- | ------------------------------------------------------------------ |
| WebSkill             | `main@e129f25fc33594dbd5eff114247b8e77738b8859`                    |
| Octop                | `develop@a01c397e`                                                 |
| octop-browser 来源   | `b3910fc50c964fabcc8a8cde6ccec7367dbb27dd`                         |
| 来源 tarball SHA-256 | `7a8b0c9eb33fc0b23e50414513e4f4ddcc45c45fae7f47e37bd8120b13bacf66` |
| 来源与本地源码       | 按排除项全树 `diff -qr` 返回 0，无源码差异                         |

`octop-browser` 目录没有 `.git`，正式发布还需上游 tag/发布记录。快照前清理 `__pycache__`、`.mypy_cache`、`.pytest_cache`、`.ruff_cache`，不能用旧缓存推断当前源码。

## 3. P0 探针覆盖

| 探针         | 目标                                                              | 关联 AC                    | 允许的结论                                             |
| ------------ | ----------------------------------------------------------------- | -------------------------- | ------------------------------------------------------ |
| 安全上下文   | 普通远程 HTTP 与 HTTPS/localhost 对比 OPFS、WebCrypto、审计、验签 | AC-35、AC-49               | HTTP 显式拒绝；HTTPS 可用                              |
| DOM/ref      | DOM 插入、导航、SPA 和 ref 失效                                   | AC-01～AC-03               | 证明现状支持或存在缺口                                 |
| 同源 frame   | 读取、点击、子 frame 导航                                         | AC-04、AC-06～AC-08、AC-47 | 证明同源能力与限制                                     |
| OOPIF        | 真正的跨 site iframe target、attach、执行上下文和动作             | AC-05～AC-08               | 现状不支持可作为 P0 结论；不得用同 site 跨 origin 冒充 |
| 新 target    | 普通链接、window.open、active 变化                                | AC-09～AC-13               | 记录实际 target 与可见焦点                             |
| 双指针/输入  | viewTarget 与 modelTarget 分离，输入钉住视图                      | AC-11、AC-12、AC-42、AC-43 | 证明可分离或需改造                                     |
| 后台出帧     | 非 active viewTarget 连续 60 秒真实出帧                           | AC-45                      | 失败需明确暂停/提示                                    |
| daemon/lease | 主进程与 record daemon 并发写、超时和崩溃                         | AC-15、AC-40               | 无共享 lease 时必须报告缺口                            |
| harness      | 取得匹配源码，验证 browser_use/eval_js 的实际路径                 | AC-60                      | 不从依赖声明猜实现                                     |
| viewer       | HTTP sandbox、CSP、模块 CORS、真实 contentWindow 握手             | AC-27、AC-28               | 握手失败则产物投放不开放                               |
| 候选转换     | 模板生成、无 steps、客户端校验、sink 提交                         | AC-22、AC-53～AC-55        | 不调用模型、不伪造轨迹                                 |
| ReplayRunner | 注入执行器与旧调用兼容                                            | AC-57、AC-58               | 增强路径不得直绕 browser_tool                          |
| 版本/契约    | API snapshot 与撤回版本不可复用                                   | AC-48                      | 未经批准 diff 必须为 0                                 |

**明确不在 P0 执行的后续项**：AC-52 的双宿主发布后技能执行、AC-25～AC-28 的完整文档产物/外部程序打开均属 P3，本轮标为未验证；任何已有扩展测试都不能冒充增强版验收。P0 表中的每项都必须有真实执行结果。

## 4. 执行环境

### 4.1 独立测试状态

- 使用临时浏览器 profile 和临时目录。
- 使用独立 HTTP/HTTPS 端口和独立 Octop `OCTOP_HOME`（如需真实服务）。
- 不读取真实用户 Cookie、登录态、模型密钥或已有 profile。
- 不使用旧 WebSkill 网关或专用 iframe 扩展。
- 本地模型若使用，默认目标为 `http://localhost:1234/v1`，具体可用性以实际请求为准。

### 4.2 网络与浏览器

- 需要至少一个普通局域网 HTTP 地址和一个 HTTPS 地址。
- HTTPS 测试证书的忽略只限 Playwright 的隔离测试上下文，不是生产配置建议。
- 后台出帧测试需要实际经过 60 秒，不能缩短后仍声称 AC-45 通过。
- K8s、多用户、HPA/Pod 重建等不得以本地单进程结果代替。

## 5. 当前探针命令

前置：在 WebSkill SDK 仓库执行 `pnpm install --frozen-lockfile`、`pnpm build`、`pnpm exec playwright install chromium`，真实 Chrome/Chromium 可用，Octop 虚拟环境包含匹配的 octop-browser 与 octop-harness。探针和证据均位于 **Octop 仓库**；SDK 默认取相邻的 `web-skill-sdk` checkout，也可用 `WEBSKILL_SDK_ROOT` 指定其他路径。

以下命令从 Octop 仓库根目录执行；已有 harness 源码时跳过重复 clone。契约探针先执行，不与其他探针并行修改/读取构建产物：

```bash
node scripts/webskill-p0/contract_probe.mjs
mkdir -p .artifacts/webskill-p0/sources
git clone --depth 1 --branch v1.0.9 https://github.com/TencentCloud/octop-harness.git .artifacts/webskill-p0/sources/octop-harness
.venv/bin/python scripts/webskill-p0/cdp_probe.py
node scripts/webskill-p0/browser_probe.mjs
```

- Python 探针使用真实系统 Chrome，负责 DOM/ref、跨 site frame/OOPIF、target、60 秒后台出帧、daemon、跨进程写入、harness 源码比对与 ReplayRunner 基线。
- Node 探针使用 SDK 开发依赖中的 Playwright Chromium，负责普通局域网 HTTP、HTTPS/localhost、真实 OPFS/审计/验签、模型 CORS/LNA、真实 contentWindow viewer 与候选 sink；它与 Python 探针的浏览器版本不同，兼容矩阵分别记录。
- 契约探针负责真实 SDK build、API snapshot、Git 无差异与已知撤回版本查询，不分配版本、不发布。
- 浏览器探针读取 Python 探针真实生成的 `recording-source.json`，因此顺序不能颠倒。
- 本机模型默认 `http://localhost:1234/v1` / `qwen3.5:9b`，可用 `OCTOP_P0_MODEL_URL` / `OCTOP_P0_MODEL_NAME` 覆盖；不可用必须报告，不启用 mock。
- 本轮真实结果：`.artifacts/webskill-p0/` 下的 `contract-probes.json`、`cdp-probes.json` 与 `browser-probes.json`；每轮 profile 独立，Chrome 与 daemon 只由该探针清理。

## 6. 证据格式

每次探针至少记录：

```json
{
  "probe": "probe-name",
  "status": "supported | unsupported | failed",
  "acStatus": "not-assessed | not-applicable | gap | failed",
  "actual": "实际观察结果",
  "commands": ["可复现命令"],
  "ac": ["AC-XX"],
  "environment": "真实组件与版本",
  "evidence": ["日志、结构化 JSON 或截图路径"]
}
```

- `supported` 只表示该探针实测成立，不表示整个组合已支持生产；`acStatus` 独立记录 AC 缺口/未评估状态。
- `unsupported` 是合法的技术结论，需要进入设计缺口，不得改成 passed。
- `failed` 表示探针本身或环境失败，需要修复后重跑；任何 `failed` 都使脚本以非零状态退出。
- 日志不得包含 token、Cookie、API key、完整 prompt 或敏感输入值。

## 7. P0 退出条件

1. 每项探针都有真实执行结果和可复现命令。
2. 明确哪些能力现有底层支持、哪些必须修改 octop-browser / harness / Octop。
3. 安全上下文、frame/OOPIF、双指针、后台出帧和跨进程写入都有实际结论。
4. 候选转换、ReplayRunner 注入和两类执行语义没有未裁决的公共契约问题。
5. 未验证项明确标为未验证，不把设计文档闭环当成验收闭环。
6. 结果满足后，才允许把对应组合从 candidate 推进到后续验收；不能自动标为 supported。

## 8. 本轮审定摘要（2026-11-16）

本轮共 32 项真实探针：21 项 `supported`、11 项 `unsupported`、0 项 `failed`。`supported` 只描述原语/基线事实，不是 AC 通过；`acStatus` 是独立的验收语义。CDP / Browser / Contract 分别对应 §5 中的同名脚本命令。完整 JSON 仍只保存在本地 `.artifacts/webskill-p0/`，本表是随代码复核的审定摘要，不是每轮自动生成的流水记录。

AC-52 与 AC-25～AC-28 仍为 P3 未验证；K8s、多用户、HPA/Pod 重建、真实 CI 和外部 PowerPoint/WPS 打开不在本轮通过范围内。

| 探针                                  | 命令     | 原语状态    | AC 状态        | 结论 / 缺口                                                                                                                  | 归属 / 阶段                          |
| ------------------------------------- | -------- | ----------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| api-snapshot-and-version-discipline   | Contract | supported   | not-assessed   | 本地 build/API snapshot 无 Git 差异；RELEASING 明确 0.28.0 未发布且 registry 仍不存在；CI 未运行，AC-48 仍 not-assessed。    | WebSkill / P0；CI 待真实执行         |
| dom-insertion-ref-identity            | CDP      | supported   | not-assessed   | DOM 插入后旧 ref 仍指向原节点，计数为 2/0。                                                                                  | octop-browser / P1 复用              |
| navigation-invalidates-ref            | CDP      | supported   | not-assessed   | 导航后旧 ref 被 Chromium 拒绝，无坐标兜底。                                                                                  | octop-browser / P1 复用              |
| same-origin-frame-read-click          | CDP      | unsupported | gap            | 当前 DOM projection 不含可操作同源 frame ref。                                                                               | octop-browser / P2                   |
| child-frame-navigation-ref            | CDP      | unsupported | gap            | 现有 API 无 frame ref，子 frame 导航失效尚未实现。                                                                           | octop-browser / P2                   |
| same-origin-cdp-pierce                | CDP      | supported   | not-assessed   | pierce=true 可见同源 frame 内容，证明是 projection 缺口。                                                                    | octop-browser / P2 复用              |
| oopif-projection-and-action           | CDP      | unsupported | gap            | 跨 site 产生独立 iframe target 且 contentDocument=false；当前 projection 不可操作。                                          | octop-browser / P2                   |
| oopif-attach-execution                | CDP      | supported   | not-assessed   | 真实 attach 后固定默认执行上下文；CDP 鼠标按下/释放使 click、pointerdown 各发生一次；非 BrowserSession 能力。                | octop-browser / P2 增加 session 支持 |
| new-target-blank                      | CDP      | supported   | gap            | 新 target 创建成功并记录新旧 visibility；工作集准入未实现。                                                                  | Octop + octop-browser / P2           |
| new-target-window-open                | CDP      | supported   | gap            | window.open 创建成功；现有 session 不自动跟随，需 modelTarget 适配。                                                         | Octop + octop-browser / P2           |
| dual-target-primitive                 | CDP      | supported   | gap            | 独立 CDP 连接在后台 target 点击一次成功；BrowserTargetSession 未实现。                                                       | Octop + octop-browser / P2           |
| background-view-60-seconds            | CDP      | supported   | not-assessed   | 完整 60 秒、真实内容变化，最大帧间隔约 1.001 秒，低于 1.5 秒探针上限。                                                       | Octop / P2                           |
| dual-target-primitive-execution       | CDP      | supported   | not-assessed   | 60 秒后两个独立连接均真实响应，非恒定通过。                                                                                  | octop-browser / P2                   |
| harness-browser-use-eval-js           | CDP      | supported   | gap            | eval_js 真实到达 browser_tool；无用户 grant/共享 lease，AC-60 是 gap。                                                       | octop-harness + Octop / P2           |
| harness-source-match                  | CDP      | supported   | not-assessed   | 双向 diff 221 个文件、清单 hash 和固定 commit 断言通过。                                                                     | octop-harness / P0 基线              |
| cross-process-write-exclusion         | CDP      | unsupported | gap            | 两个进程 DOM 写入和两个独立 POST 均完成；barrier 由 Barrier.wait 直接记录为 true，到达间隔小于 0.5 秒；无共享 lease。        | octop-browser + Octop / P2           |
| octop-recorder-template-baseline      | CDP      | supported   | not-assessed   | 隔离录制与 Octop Bash 模板确定性成立；不代表 WebSkill 候选通过。                                                             | octop-browser / P3 适配              |
| replay-runner-executor-injection      | CDP      | unsupported | gap            | 旧默认路径通过；构造参数只有 store，授权执行器注入未实现。                                                                   | octop-browser + Octop / P3           |
| recording-retention-and-delete        | CDP      | unsupported | gap            | 无 delete、retention 和容量限制；30 天/50 份是待实现目标。                                                                   | octop-browser + Octop / P3           |
| daemon-crash-lease                    | CDP      | unsupported | gap            | 确认 daemon 停止响应后仍能写；无 lease/grant 失效机制。                                                                      | octop-browser + Octop / P2/P3        |
| security-localhost                    | Browser  | supported   | not-assessed   | localhost 下 OPFS 往返、审计链、验签与篡改拒绝通过；非完整 AC。                                                              | Octop / P1                           |
| security-remote-http                  | Browser  | supported   | gap            | 普通 LAN HTTP 不是安全上下文，OPFS/WebCrypto 不可用；应用拒绝未实现。                                                        | Octop / P1 门禁                      |
| security-remote-https                 | Browser  | supported   | not-assessed   | HTTPS 下 OPFS、审计和验签可用；挂载前准入仍待实现。                                                                          | Octop / P1                           |
| model-cors-and-real-completion        | Browser  | supported   | not-assessed   | 真实 qwen3.5:9b 完成 P0_OK，有实际 usage；CORS 可用。                                                                        | Octop / P1                           |
| model-no-auth-configuration           | Browser  | unsupported | gap            | SDK 仍要求非空 key；P1 由宿主内部非秘密占位兼容，不修改 SDK。                                                                | Octop / P1                           |
| model-lna-permission-query            | Browser  | supported   | not-assessed   | 权限查询返回 prompt；拒绝路径未验证，不能冒充 LNA 验收。                                                                     | Octop / P1 补拒绝场景                |
| https-dashboard-to-http-model         | Browser  | supported   | not-assessed   | HTTPS LAN 页可读取 localhost /models；不把 localhost 例外推广为任意 HTTP。                                                   | Octop / P1                           |
| viewer-http-sandbox-csp-and-handshake | Browser  | unsupported | gap            | 朴素 iframe 因 opener=null 握手失败；需真实 parent 宿主适配。                                                                | Octop / P3                           |
| viewer-close-lifecycle                | Browser  | unsupported | gap            | close() 不删除宿主 iframe；宿主 dispose/回收待实现。                                                                         | Octop / P3                           |
| viewer-real-parent-host-adapter       | Browser  | supported   | not-assessed   | 真实 parent + contentWindow 握手、隔离和数据投递成功，无需公共 API 变更。                                                    | Octop / P3 复用                      |
| candidate-sink-primitive              | Browser  | supported   | not-applicable | 仅 sink/canonical 向量成立；键序无关、未知字段与非有限数字拒绝、有限数字保留，提交对象与 digest 输入一致；非 WebSkill 适配。 | WebSkill 既有端口 / P3 复用          |
| candidate-adapter                     | Browser  | unsupported | gap            | 固定 WebSkill 模板、严格输入校验和跨端 digest 未实现；Bash 原文不得提交。                                                    | Octop / P3                           |
