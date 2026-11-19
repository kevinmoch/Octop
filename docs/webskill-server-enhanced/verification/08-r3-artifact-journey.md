# R3 产物与隔离 viewer 旅程

> 状态：🟢 **R3 本地 candidate 通过**：产物/viewer、真实模型 DOCX、Console/审计/刷新、固定 profile 服务重启恢复均有证据。
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
- 已用固定持久化 profile 完成服务重启恢复；刷新/重开/Console 运行与审计也已通过。仍未完成多格式真实模型与 Office GUI 人工验收。
- 没有把 R3 文档场景与 Console 审计/治理串联成同一真实模型任务。
- R3 仍有 viewer CSP/关闭/资源清理的基础证据，但不等于完整产品旅程。

## 4. 结论

**R3 的产物交付与隔离 viewer 切片是本地 candidate**，但不能宣称 R3 整体完成。
R3 整体仍需：真实外部模型文档任务 + Console 审计串联 + 刷新/重开/重启恢复。

> 未推送远端、未创建远端分支；未修改/发布 SDK。

## 5. 真实外部模型文档任务（2026-11-19 补充）

命令：

```bash
WEBSKILL_P1_UI_URL=http://127.0.0.1:18089 \
WEBSKILL_P1_MODEL_URL=http://localhost:1234/v1 \
UV_NO_SYNC=1 ... python scripts/webskill-p1/r3_real_model_document_acceptance.py
```

结果：

```json
{
  "status": "passed",
  "model": "qwen3.5:9b",
  "modelMode": "external",
  "modelRequests": 4,
  "skill": "authored-bulletin",
  "viewerOpened": true,
  "markerVisible": true,
  "pageErrors": []
}
```

链路：

1. 真实模型读取 `authored-bulletin` 的 `SKILL.md`。
2. 模型触发 `ask_user` 表单，填写数据来源与内容要求。
3. 模型调用技能 `publish`，生成 DOCX 文档。
4. 用户确认打开文档；真实 viewer 中看到标题 `Octop real-model bulletin`。
5. 页面错误为 0。

这补上了“真实外部模型文档任务”的证据，但仍是单一 DOCX 场景，不是 Office GUI 人工验收。

### Console / refresh 补充

- `scripts/webskill-p1/r3_console_journey_acceptance.py` 使用同一真实模型 DOCX 场景，关闭 viewer 后打开 Console：
  - `runsText` 出现 `authored-bulletin` 运行记录；
  - `auditText` 出现 `document.surface.open` 审计事件；
  - `page.reload` 后 Chatbot 仍可加载；
  - `consoleRunsObserved=true`、`auditObserved=true`、`reloadObserved=true`、`pageErrors=[]`。
- 证据：`.artifacts/webskill-p1/ui/ui-result-r3-console-journey.json`。
### 固定 profile 服务重启恢复

- `viewer_acceptance.py` 支持 `WEBSKILL_PERSISTENT_PROFILE`，用固定 `user-data-dir` 跑 bulletin 文档。
- 停止 UI 服务、重启后，用同一 profile 运行 `r3_restart_phase2.py`：
  - Console 运行记录：`run-eeffp1r2`，`completed`，`authored-bulletin`；
  - 审计：`document.surface.open`；
  - `installedSkillObserved=true`、`consoleRunObserved=true`、`auditObserved=true`；
  - `pageErrors=[]`。
- 证据：`.artifacts/webskill-p1/ui/ui-result-r3-restart-phase2.json`。
- 该恢复验收使用脚本化 bulletin fixture；真实模型 DOCX 已在同文档前文单独通过。

**R3 本地 candidate 剩余边界**：多格式 DOCX/PPTX/XLSX 的真实外部模型验收、Office GUI 人工验收。

## 6. PDF 整页渲染（补充）

- `scripts/webskill-p1/pdf_render_acceptance.py` 使用真实 Chromium + 真实 PDF.js build。
- 结果：`PDF_RENDER_OK`，canvas 600×400，非白像素 918，PNG 11538 bytes，页面错误 0。
- 证据：`.artifacts/webskill-p1/ui/ui-result-pdf-render.json`、`pdf-render.png`。
- 这验证 PDF.js 渲染路径，不等于 PDF 图片投放（图片进入模型上下文）已通过。
