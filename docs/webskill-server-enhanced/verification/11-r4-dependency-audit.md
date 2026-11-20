# R4 依赖安全审计（本地）

> 状态：🔴 未通过生产安全准入。  
> 日期：2026-11-20  
> 命令：`npm audit --omit=dev --json`（dashboard）

## 结果

- total: 15
- critical: 2
- high: 4
- moderate: 3
- low: 6

主要项：

- `js-yaml` critical/high/moderate（多种 advisory）
- `uglify-js` critical/high（jxLoader 间接）
- `react-router` high/moderate（open redirect/DoS 等）
- `xlsx` high（prototype pollution/ReDoS）
- `mermaid` / `katex` / `dompurify` 多项 moderate/low

## 判定

- 这些是发布前依赖审计项，不是本轮 WebSkill 功能测试通过就能豁免的。
- 未执行强制升级/覆盖，避免把不兼容变更混进 candidate。
- 生产/上游发布前需要逐项评估：升级、替换、隔离或书面风险接受。
- 当前不能宣称生产安全准入通过。

## 扩展仓库

`pnpm audit --prod --json`：

- total: 35
- critical: 0
- high: 9
- moderate: 7
- low: 19

扩展侧同样未清零，不能据此做生产发布。

## 备注

- 之前 P3 已处理 `image-size` 两项告警；本清单是剩余既有依赖告警。
- 依赖升级需要逐项回归，不能为了“清零”直接覆盖 lockfile。
