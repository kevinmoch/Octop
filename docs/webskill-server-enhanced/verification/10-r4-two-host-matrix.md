# R4 两宿主能力矩阵

> 状态：🟡 基础能力有服务端/扩展宿主证据；**完整两宿主对等未通过**。  
> 日期：2026-11-19  
> 目标：把“服务端宿主已通过”与“扩展宿主也能做”分开记录，不用单宿主结果冒充完整能力。

## 1. 矩阵

| 能力 | 服务端宿主 | 扩展宿主 | 判定 |
|---|---|---|---|
| 主文档 / frame / OOPIF 感知与动作 | R1/R2/R3 真实页面 | P3 已发布技能执行真实点击 | 基础通过 |
| 受限回放 | P3 真实 ReplayRunner | 未重验 | 服务端单侧 |
| 原生技能生成 | R1 真实模型（DOCX 闭环） | 未重验 | 服务端单侧 |
| 已发布技能执行 | P3/R1 | P3 扩展宿主真实执行 | 基础通过 |
| pageTabs / paging | R2A 真实 UI + 模型 | 未重验 | 服务端单侧 |
| pageImage | R2B 主文档真实；OOPIF 单测 | 未重验 | 服务端单侧 |
| PDF | R3 真实附件 DOCX；PDF reader 未完整 | 未重验 | 服务端单侧 |
| downloads | R2B 真实下载 + Chatbot list/read | 未重验 | 服务端单侧 |
| DOCX/PPTX/XLSX/screen/viewer | R3 真实模型 DOCX；其余脚本化 | 未重验 | 服务端单侧/脚本化 |
| 原生扩展特权 API | 不可用 | 需扩展 | 明确差异 |
| WebOffice/DWG/remoteImage | unsupported | 未验证 | 默认不支持 |

## 2. 结论

- **不能宣称“两宿主完整能力对等”**。
- 服务端宿主已有 R1/R2/R3 的真实模型和真实页面证据；扩展宿主主要证明已发布技能执行。
- R4 仍需扩展宿主的 pageTabs/paging/pageImage/PDF/downloads/文档矩阵，否则只能算服务端 candidate + 扩展基础候选。
- 测试 CA、历史 K8s 证据、单宿主结果都不能替代两宿主对等。

## 3. 下一步

1. 对扩展宿主执行 R2A/R2B 的最小页面/资源矩阵；
2. 对服务端和扩展宿主使用同一 fixture、同一授权、同一语义断言；
3. 每个能力单独记录 `supported/partial/unsupported`；
4. 未通过项不进入支持矩阵的 `supported`。
