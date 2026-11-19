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
