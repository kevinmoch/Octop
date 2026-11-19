"""Render a PDF page with the real PDF.js build in Chromium."""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
PDFJS_BUILD = ROOT / "dashboard/webskill/node_modules/pdfjs-dist/build"
PDF_BASE64 = "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Ci9Gb250IDw8Ci9GMSA8PAovVHlwZSAvRm9udAovU3VidHlwZSAvVHlwZTEKL0Jhc2VGb250IC9IZWx2ZXRpY2EKPj4KPj4KPj4KL01lZGlhQm94IFsgMC4wIDAuMCAzMDAgMjAwIF0KL1BhcmVudCAyIDAgUgovQ29udGVudHMgPDwKL0xlbmd0aCA0MAo+PgpzdHJlYW0KQlQgL0YxIDEyIFRmIDIwIDEwMCBUZCAoSGVsbG8gUERGKSBUaiBFVAplbmRzdHJlYW0KPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAwMDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE2MiAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDUKL1Jvb3QgMyAwIFIKL0luZm8gMSAwIFIKPj4Kc3RhcnR4cmVmCjQxMgolJUVPRgo="
HTML = """<!doctype html><html><body><canvas id="page"></canvas>
<script type="module">
import * as pdfjs from "./pdf.mjs";
pdfjs.GlobalWorkerOptions.workerSrc = "./pdf.worker.mjs";
const bytes = Uint8Array.from(atob("__PDF_BASE64__"), c => c.charCodeAt(0));
const pdf = await pdfjs.getDocument({data: bytes}).promise;
const page = await pdf.getPage(1);
const canvas = document.getElementById("page");
const viewport = page.getViewport({scale: 2});
canvas.width = viewport.width; canvas.height = viewport.height;
const context = canvas.getContext("2d");
await page.render({canvasContext: context, viewport}).promise;
const image = canvas.toDataURL("image/png");
const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
let nonWhite = 0;
for (let i = 0; i < pixels.length; i += 4) {
  if (pixels[i] < 250 || pixels[i + 1] < 250 || pixels[i + 2] < 250) nonWhite++;
}
document.title = image.startsWith("data:image/png;base64,") && nonWhite > 0 ? "PDF_RENDER_OK" : "PDF_RENDER_EMPTY";
window.__result = {width: canvas.width, height: canvas.height, nonWhite, bytes: image.length, image};
</script></body></html>""".replace("__PDF_BASE64__", PDF_BASE64)


async def run() -> dict[str, Any]:
    temp = Path(tempfile.mkdtemp(prefix="octop-pdf-render-"))
    try:
        for name in ("pdf.mjs", "pdf.worker.mjs"):
            shutil.copyfile(PDFJS_BUILD / name, temp / name)
        (temp / "index.html").write_text(HTML, encoding="utf-8")
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(temp))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        errors: list[str] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
            )
            page = await browser.new_page(viewport={"width": 900, "height": 700})
            page.on("pageerror", lambda error: errors.append(safe_error(error)))
            try:
                await page.goto(url, wait_until="networkidle")
                await page.wait_for_function(
                    "document.title.startsWith('PDF_RENDER_')", timeout=30_000
                )
                result = await page.evaluate("window.__result")
                data_url = result.pop("image")
                (OUT / "pdf-render.png").write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
                return {
                    "status": "passed" if await page.title() == "PDF_RENDER_OK" else "failed",
                    "title": await page.title(),
                    **result,
                    "pageErrors": errors,
                }
            finally:
                await browser.close()
    finally:
        server.shutdown()
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-pdf-render.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
