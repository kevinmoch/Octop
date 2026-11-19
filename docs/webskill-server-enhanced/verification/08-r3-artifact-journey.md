# R3 产物与隔离 viewer 旅程

> 状态：🟡 **产物交付/隔离 viewer 本地 candidate 通过**；完整 R3 旅程尚未完全收口。  
> 日期：2026-11-19  
> 模型：本轮文档场景仍为脚本化模型（`modelMode=scripted`），不是外部模型文档生成验收。  
> 证据：`.artifacts/webskill-p1/ui/ui-result-viewer.json`、`ui-result-document-bulletin.json`、`ui-result-document-slides.json`、`ui-result-document-spreadsheet.json`。

## 1. 本轮命令

```bash
WEBSKILL_P1_UI_URL=http://127.0.0.1:18089 \
WEBSKILL_DOCUMENT_CASE=<screen|bulletin|slides|spreadsheet> \
UV_NO_SYNC=1 ... python scripts/webskill-p1/viewer_acceptance.py
```

四个 case 的实际结果：

| case | 结果 | 关键字段 |
|---|---|---|
| `screen` | passed | `viewerOpened=true`、`opaqueOrigin=true`、`storageBlocked=true`、`parentAccessBlocked=true`、`closeRemovedDom=true` |
| `bulletin` | passed | `exportFormat=docx`、`reopenedWith=python-docx`、`pdfVerified=true`、`pdfReopenedWith=pypdf` |
| `slides` | passed | `exportFormat=pptx`、`reopenedWith=python-pptx`、`printPagesVerified=3` |
| `spreadsheet` | passed | `reopenedWith=openpyxl`、`contentVerified=true` |

四个 case 的 `pageErrors=[]`；screen/bulletin/slides 的 `consoleErrors=[]`；spreadsheet 仅有 openpyxl 的默认样式警告，不是页面错误。

## 2. 已证明

- 文档/大屏产物真实生成、真实下载、真实文件解析器重开；
- DOCX/PPTX/XLSX/PDF 文本与结构校验；
- 中文大屏的 HTML/CSS 生效；
- viewer 使用 opaque origin，storage 与 parent 访问被隔离；
- wrong source/channel 被拒绝；
- 关闭 viewer 删除宿主 DOM；
- XLSX 从 OPFS 取出真实工作簿并 re-open。

## 3. 未完成 / 边界

- 文档场景使用**脚本化模型**驱动真实工具链；没有用真实外部模型生成文档。
- 没有覆盖 Word/PowerPoint/Excel 的 GUI 人工验收；文件可打开由 Python 解析器验证。
- 没有把幻灯片原生 PDF 设为通过；只验证 PPTX + 3 页打印 DOM。
- 没有完成刷新 Dashboard、重开 Console 运行、服务重启后的完整恢复旅程。
- 没有把 R3 文档场景与 Console 审计/治理串联成同一真实模型任务。
- R3 仍有 viewer CSP/关闭/资源清理的基础证据，但不等于完整产品旅程。

## 4. 结论

**R3 的产物交付与隔离 viewer 切片是本地 candidate**，但不能宣称 R3 整体完成。  
R3 整体仍需：真实外部模型文档任务 + Console 审计串联 + 刷新/重开/重启恢复。

> 未推送远端、未创建远端分支；未修改/发布 SDK。
