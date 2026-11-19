# R2B 页面图片：真实 UI + 模型验收

> 状态：🟢 **R2B 本地 candidate 通过**：pageImage、PDF、downloads 均有真实证据；高风险边界见 R2C。
> 日期：2026-11-19
> 模型：本地 `qwen3.5:9b`；证据：`.artifacts/webskill-p1/ui/ui-result-r2b-page-image.json`。

## 1. 实现

- 后端 `capture_image`：
  - 只接受一个来自 fresh `perceive_page` 的 ref 与正字节预算；
  - `DOM.scrollIntoViewIfNeeded` + `DOM.getBoxModel` + `Page.captureScreenshot`；
  - 主文档真实通过；OOPIF 通过 `attach_frame_expected` 在目标 frame client 上取像并关闭 attachment；same-origin child frame 仍明确返回 unsupported；
  - 超过预算返回 failure，不截断；
  - 不暴露服务器路径、选择器、URL 或任意 CDP。
- WebSocket 协议新增 `capture-image` / `capture-image-result`；transport 解析。
- 宿主 `pageImage: createRemotePageImageHost({ transport })`。
- 走 Chatbot 的 `capture_page_image` 授权面；图片落会话 artifact，不直接进用户磁盘。

## 2. 真实验收

```bash
WEBSKILL_P1_UI_URL=http://127.0.0.1:18089 \
WEBSKILL_P1_MODEL_URL=http://localhost:1234/v1 \
UV_NO_SYNC=1 ... python scripts/webskill-p1/r2b_page_image_acceptance.py
```

结果：

```json
{
  "status": "passed",
  "model": "qwen3.5:9b",
  "imageResult": {
    "mimeType": "image/png",
    "width": 240,
    "height": 160,
    "bytes": 2099
  },
  "modelRequests": 5,
  "pageErrors": []
}
```

- 模型真实调用 `capture_page_image`；
- 用户授权后返回 PNG；
- 结果只含图片事实，不含路径；
- 验收 fixture 当前把取像目标渲染成一个 240×160 的按钮（页面图片工具的输入是一个 element ref），所以证据是“控件截图”而不是一张 `<img>` 标签；这不改变 ref/预算/授权链路的结论。

## 3. 测试

```bash
UV_NO_SYNC=1 ... pytest tests/unit/api/test_webskill_capture.py -q
# 2 passed
```

覆盖：主文档取像、尺寸/字节、ref 过期、超预算拒绝。

## 4. 未完成

- PDF 直传/逐页读取；
- downloads 当前会话完成项；
- 子 frame / OOPIF 取像；
- 真实 `<img>`、canvas、SVG 分别取像；
- 取消、跨 target、图片压缩归一化与文档投放闭环。

## 5. 边界

- pageImage 是本地 candidate，不是 production/上游发布；
- 只验证主文档 element screenshot；不能宣称任意 frame/图片格式已覆盖；
- 未推送远端、未创建远端分支；未修改/发布 SDK。

## 6. PDF 文本回退（部分）

- 新增 `POST /api/browser-agent/pdf/extract`：
  - 只接收 base64 字节，不接收路径/URL；
  - same-origin + `browser` 权限；
  - 20 MB 上限；
  - pypdf 逐页 `extract_text()`，超过 200000 字符截断并明确标记；
  - 返回 text/pages/truncated，不返回服务器路径。
- 宿主 `pdfExtractor` 通过 bridge 调用该端点；未接 `pdfDocumentReader`。
- 测试：`tests/unit/api/test_webskill_pdf.py` 2 passed（文本、页数、坏 base64、跨源拒绝）。

**已补真实附件验收**：Chatbot 上传 `bulletin.pdf`，真实模型调用 `read_document`，`pdfjs-dist@6.5.438` reader 的 PDF 文本进入模型上下文（`pdfTextObserved=true`）；`pdf-input` 升为 candidate。
**仍缺**：PDF 整页渲染图/图片投放、加密/超大 PDF、取消的真实 UI 矩阵。

## 7. downloads

- 后端会话级下载目录：`Browser.setDownloadBehavior` 指向临时目录，监听 will-begin/progress；完成后移到 opaque id，列表/读取不暴露路径。
- 宿主 `DownloadedFileReader` 接 `downloads-list` / `downloads-read`；`Console hostCapabilities.downloadedFiles` 改为 `true`。
- 单测覆盖 completed → list/read、未知 id、清理（2 passed）。
- **真实下载 + Chatbot 工具**：
  - 真实页面点击 Download report；
  - 会话目录出现 `report.txt`（19 bytes，text/plain，opaque id）；
  - 模型真实调用 `list_downloaded_files` 与 `read_downloaded_file`；
  - 两次授权卡均提交；
  - 下载文本进入模型上下文（`downloadTextObserved=true`）；
  - 3 次模型请求，0 页面错误。
  - 证据：`.artifacts/webskill-p1/ui/ui-result-r2b-downloads-host.json`。
- 服务端直连证据另见 `ui-result-r2b-downloads-backend.json`（27 bytes 对账）。
- 剩余边界：下载过期/跨用户/超大文件/删除矩阵仍未单独覆盖；当前只验证当前会话已完成项。
