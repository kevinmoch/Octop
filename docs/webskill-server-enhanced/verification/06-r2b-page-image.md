# R2B 页面图片：真实 UI + 模型验收

> 状态：🟢 pageImage 本地 candidate；🟡 PDF/downloads 仍未完成。  
> 日期：2026-11-19  
> 模型：本地 `qwen3.5:9b`；证据：`.artifacts/webskill-p1/ui/ui-result-r2b-page-image.json`。

## 1. 实现

- 后端 `capture_image`：
  - 只接受一个来自 fresh `perceive_page` 的 ref 与正字节预算；
  - `DOM.scrollIntoViewIfNeeded` + `DOM.getBoxModel` + `Page.captureScreenshot`；
  - 只支持主文档；子 frame 明确返回 unsupported，不静默拿别的 frame；
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
