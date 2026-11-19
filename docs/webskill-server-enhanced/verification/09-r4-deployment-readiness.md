# R4 部署与发布准入

> 状态：🔴 **未达到生产/上游发布准入**。R4 只做只读就绪审计，不把 candidate 写成 supported。  
> 日期：2026-11-19  
> 基线：Octop `5a8d462f` + R2/R3 candidate；browser wheel `1.1.0+webskill.local.2`。  
> 原则：未推送远端、未创建远端分支、未创建发布 tag。

## 1. 就绪矩阵

| Gate | 当前结果 | 证据/原因 | 判定 |
|---|---|---|---|
| 两宿主基础链路 | 服务端 + 扩展宿主均已执行过已发布技能/页面动作 | P3 `ui-result-skill-execution.json`；R1/R3 服务端真实模型 | 部分通过 |
| 两宿主完整能力对等 | 未做 R4 全矩阵 | R3 文档真实模型只覆盖 DOCX；扩展宿主文档能力未重验 | ❌ |
| 普通受信 TLS | **失败** | `ui-result-trusted.json` 仍为 `status=failed`；证书/登录/推流已进入，模型回复等待超时 | ❌ |
| LNA 拒绝 | 通过（隔离受信 TLS） | `ui-result-trusted-lna-permission.json`、`ui-result-trusted-lna-policy.json`，`modelRequests=0` | ✅（限定范围） |
| Docker 新 wheel 构建 | **未完成** | 本次 `docker build -f deploy/docker/Dockerfile --build-arg OCTOP_IMAGE_VARIANT=lite` 在 `node:20-slim` 拉取阶段超过验收窗口，未进入 wheel 安装 | ❌ |
| K8s 单副本 PVC | 通过（P2 历史证据） | `k8s/persistent-result.json`：Pod UID 更换、旧会话 404、Cookie 保留、新会话可读/操作 | ✅（历史证据） |
| K8s 双副本 emptyDir | 通过（P2 历史证据，限定） | `k8s/emptyDir-result.json`：affinity、Pod 删除后旧会话 404、另一副本 `anonymous` 可重登；`crossPodLockClaimed=false` | ✅（限定） |
| 多 worker/多副本回放授权 | **未通过** | `ReplayRegistry`、control owner 仍是进程内状态；R4 未做跨 worker 共享/失效 | ❌ |
| 依赖安全 | 仍有告警 | image-size 两项已处理；PDF.js/其他既有依赖告警未清零；不能作生产安全准入 | ❌ |
| 支持矩阵一致性 | 机器清单/UI/文档已对齐 | 19 candidate、1 partial（能力状态 UI）、5 unsupported；R2C 决策完成 | ✅（内部一致） |
| 发布声明 | 未批准 | 无 remote push、无 tag、无 upstream PR | ❌ |

## 2. 已确认的 R4 事实

- **普通受信 TLS 不是 P3 viewer/LNA 能替代的 gate**：旧的 `ui-result-trusted.json` 仍是失败证据，不能写通过。
- **Docker 未验证**：本次构建停在基础镜像拉取；没有“新 wheel 在容器内被安装”的证据。
- **K8s 历史证据有效但有限**：单副本 PVC 与双副本 emptyDir 证明的是会话/登录替换行为，不是跨 Pod 文件锁或生产容量。
- **多 worker 是硬阻塞**：回放授权、owner 缓存不能跨进程共享；R4 不能把单进程 candidate 当成多副本支持。
- **npm 依赖告警未清零**：pptxgenjs/image-size 两项已消失，但其余告警仍是生产安全审计项。

## 3. 结论

**R4 未通过。** 当前仍是本地 candidate，不是 production/上游发布。

下一步最小顺序：

1. 修普通受信 TLS 的模型超时/证书链路，重新产出 `ui-result-trusted.json=passed`；
2. 在新环境完成 `Dockerfile` 的 `node:20-slim`/wheel 安装，并跑容器健康检查；
3. 设计并实现跨 worker 的 replay 授权/owner 失效；
4. 清点/处置依赖告警；
5. 才进入发布评审。

> 本文不改变既有 FR/AC，也不把未验证项写成通过。

## 4. 本地可完成项（本轮）

- downloads 会话隔离/清理/过期：新增单测
  - `read_download` 对另一个 session 的 id 返回不可用；
  - cleanup 删除字节文件与记录；
  - 超过 TTL 的记录在 list/read 时惰性删除；
  - 当前测试 **5 passed**。
- 多格式真实模型：
  - `authored-slides` 模型实际进入 `ask_user` 表单，但表单含复选框，脚本未完成表单提交；本轮不宣称 slides 真实模型通过。
  - DOCX 真实模型证据仍为有效切片；PPTX/XLSX/PDF 的真实模型验收仍未完成。
- 两宿主矩阵：
  - 服务端宿主已有 R1/R3 真实模型 DOCX 与技能执行；
  - 扩展宿主已有 P3 已发布技能执行；
  - **文档能力在扩展宿主上仍未重验**，不能称两宿主文档对等。

## 5. 多 worker 契约（设计，未实现）

多 worker 不是“把 ReplayRegistry 换成 Redis”这么小：

1. `ReplayAuthorization`（replayId、recording digest、user/session、origin/action 白名单、TTL、step grant counter）必须落共享存储，并以原子 compare-and-consume 消费每步 grant。
2. `control owner`/profile lease 需要共享 TTL lease；进程内缓存必须失效，不能以旧 owner 放行写入。
3. 浏览器会话、target/ref 表与 WS 连接仍绑定执行 worker；共享回放授权不应让另一个 worker 直接操作不属于它的 PageAgent。
4. 多 worker 需要 sticky session/affinity 或共享浏览器会话路由；当前只验证单副本和 limited emptyDir 会话语义，`crossPodLockClaimed=false` 仍是事实。
5. 取消/过期/接管必须在共享层原子传播；否则另一个 worker 仍可能消费旧 grant。
6. 在这些契约没有实现和真实 K8s 证据前，R4 多 worker gate 保持拒绝。

## 6. R4 当前判定

- 本地可完成项：downloads 边界单测、支持矩阵、两宿主证据清点、多 worker 契约设计。
- 仍外部/基础设施阻塞：普通受信 TLS、Docker 基础镜像/新 wheel 安装、K8s 多副本、共享存储、依赖安全签核。
- **R4 仍未通过；不宣称生产可用。**
