# R2C 高风险能力决策（默认不支持）

> 状态：R2C 决策完成；没有把高风险能力实现成默认可用。
> 日期：2026-11-19
> 依据：[capability-inventory.json](../capability-inventory.json)、R0 威胁模型、P2/P3 真实验证与当前宿主能力。

## 1. 决策

| 能力 | 决策 | 理由 | 用户可见状态 |
|---|---|---|---|
| `remoteImage` 外链图片 | **unsupported / probe only** | 服务端抓任意 URL 会引入 SSRF、重定向、DNS、私有地址、端口、MIME/炸弹风险；当前不开放代理 | 明确不支持 |
| WebOffice 读取 | **unsupported** | 需要页面 hook/relay；requestkey/原件不能进入模型；无公开安全端口 | 明确不支持 |
| WebOffice 原件模板 | **unsupported** | 不能伪造原件，也不能暴露 requestkey | 明确不支持 |
| DWG | **unsupported** | 需要独立 Worker、解析预算、崩溃隔离、字体/图层安全评审 | 明确不支持 |
| 页面声明技能 / WebMCP | **unsupported** | 不能与 Octop 连接器/账本合并；页面声明不能扩大权限 | 明确不支持 |
| 远程 MCP | **not-applicable** | WebSkill 自有端点，不接 Octop 账本 | 保持独立 |

## 2. 已落实

- 能力清单中以上项均为 `unsupported`/`not-applicable`，不是 `candidate`。
- UI `HOST_CAPABILITY_GROUPS` 的“明确不支持”组包含 remoteImage、WebOffice、WebOfficeTemplate、DWG、页面声明技能。
- 没有服务端任意 URL 抓取、WebOffice 原件读取、DWG 解析或页面技能注入代码。
- 没有为这些能力扩大 CSP/`allow-same-origin` 或开放任意 CDP。

## 3. R2C 退出条件

- [x] 每项有 implement/defer/unsupported 决策和理由。
- [x] 不支持的 UI 状态与能力清单一致。
- [x] 没有未经评审的远程抓取、requestkey、DWG 解析。
- [ ] 若未来产品要求实现，必须单独立项：SSRF/重定向/私有地址、Worker/文件炸弹、requestkey、权限模型、两宿主验收。
- [ ] 不在本专项中把 R2C 能力写成“完整 WebSkill 已通过”。

## 4. 边界

- R2C 的“完成”指**决策收口**，不是高风险能力通过。
- 当前仍是本地 candidate；不推送远端、不创建远端分支。
- 不修改/发布 SDK。
