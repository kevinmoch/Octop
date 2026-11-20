"""Exercise R2 resource capabilities inside the real MV3 extension host.

The model transport is scripted so each host tool is called deterministically;
the page, extension, consent cards, downloads and PDF reader are real.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "extension"
DIST = Path("/Users/mochunhui/Git/web-skill-sdk/examples/browser-extension/dist")
PROFILE = Path("/tmp/webskill-extension-resources-profile")
PDF = ROOT / ".artifacts" / "webskill-p1" / "ui" / "bulletin.pdf"
BASE = "http://127.0.0.1:0"

PAGING_BUTTONS = "".join(f"<button>Paging control {i:04d} {'x' * 90}</button>" for i in range(1400))
PAGES = {
    "/main": """
      <title>Main fixture</title><h1>Main fixture</h1>
      <a id="dl" href="/file">Download report</a>
      <a id="detail" href="/detail" target="_blank">Open detail</a>
    """,
    "/detail": "<title>Detail fixture</title><h1>Detail fixture</h1>",
    "/paging": f"<title>Paging fixture</title><h1>Paging fixture</h1>{PAGING_BUTTONS}",
    "/image": """
      <title>Image fixture</title><h1>Image fixture</h1>
      <canvas id="pic" width="120" height="80"></canvas>
      <script>const c=document.getElementById('pic').getContext('2d');
      c.fillStyle='#d22';c.fillRect(10,10,100,60);</script>
    """,
    "/file": "HOST_DOWNLOAD_OK",
}


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = PAGES.get(self.path, "not found").encode()
        self.send_response(200 if self.path in PAGES else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if self.path == "/file":
            self.send_header("Content-Disposition", 'attachment; filename="report.txt"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def sse(delta: dict[str, Any]) -> str:
    return f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n"


def tool_call(name: str, args: dict[str, Any], call_id: str) -> str:
    return (
        sse(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ]
            }
        )
        + "data: [DONE]\n\n"
    )


def final(text: str) -> str:
    return sse({"content": text}) + "data: [DONE]\n\n"


def tool_data(body: str) -> Any:
    try:
        messages = json.loads(body).get("messages", [])
    except json.JSONDecodeError:
        return None
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                try:
                    content = json.JSONDecoder().raw_decode(content.lstrip())[0]
                except json.JSONDecodeError:
                    return content
        if isinstance(content, dict):
            items = content.get("content")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("data"), dict):
                        return item["data"]
            data = content.get("data")
            if isinstance(data, dict):
                return data
        return content
    return None


async def allowed_click(side: Any) -> None:
    allow = side.get_by_role("button", name="允许", exact=True)
    visible = [
        allow.nth(index)
        for index in range(await allow.count())
        if await allow.nth(index).is_visible()
    ]
    if visible:
        await visible[-1].click(force=True)
        await side.wait_for_timeout(250)


async def bind(side: Any, fixture: Any) -> None:
    tabs = await side.evaluate("() => chrome.tabs.query({})")
    target = next(
        (
            tab
            for tab in tabs
            if str(tab.get("url") or "").startswith("http://127.0.0.1:")
            and tab.get("url", "").endswith(fixture["path"])
        ),
        None,
    )
    assert target is not None, tabs
    tab_id = target["id"]
    await side.evaluate("(id)=>chrome.tabs.update(id,{active:true})", tab_id)
    await side.evaluate(
        "(id)=>localStorage.setItem('webskill.extension.last-bound-tab',String(id))",
        tab_id,
    )
    await side.reload(wait_until="domcontentloaded")
    await side.wait_for_timeout(1200)


async def new_sidepanel(context: Any, extension_id: str, handler: Any) -> Any:
    side = await context.new_page()
    await side.goto(
        f"chrome-extension://{extension_id}/sidepanel.html", wait_until="domcontentloaded"
    )
    await side.wait_for_timeout(800)
    await side.route("**/chat/completions", handler)
    return side


async def ask(side: Any, prompt: str, marker: str, timeout: float = 60) -> str:
    composer = side.locator("textarea:visible").last
    await composer.fill(prompt)
    await composer.press("Enter")
    end = asyncio.get_running_loop().time() + timeout
    body = ""
    while asyncio.get_running_loop().time() < end:
        await allowed_click(side)
        body = await side.locator("body").inner_text()
        if marker in body:
            return body
        await side.wait_for_timeout(400)
    return body


async def run() -> dict[str, Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_base = f"http://127.0.0.1:{server.server_port}"
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True)
    results: dict[str, Any] = {}
    errors: list[str] = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            ignore_default_args=["--disable-extensions"],
            args=[
                f"--disable-extensions-except={DIST}",
                f"--load-extension={DIST}",
                "--no-proxy-server",
                "--no-first-run",
            ],
            viewport={"width": 1100, "height": 900},
            locale="zh-CN",
        )
        try:
            await asyncio.sleep(2)
            extension_id = [worker.url.split("/")[2] for worker in context.service_workers][0]
            pages: dict[str, Any] = {}
            for name in ("main", "paging", "image"):
                page = await context.new_page()
                await page.goto(page_base + "/" + name)
                pages[name] = page
            # Tabs listing: only the bound page is in the workset.
            main = pages["main"]
            side = await new_sidepanel(context, extension_id, None)  # type: ignore[arg-type]
            await bind(side, {"path": "/main"})
            tabs_turns = {"n": 0}

            async def tabs_handler(route: Any) -> None:
                tabs_turns["n"] += 1
                n = tabs_turns["n"]
                body = route.request.post_data or ""
                if n == 1:
                    output = tool_call("list_page_tabs", {}, "call-list")
                elif n == 2:
                    data = tool_data(body) or {}
                    tabs = data.get("tabs") if isinstance(data, dict) else None
                    if not tabs:
                        output = final("TABS_NO_RESULT")
                    else:
                        output = tool_call("focus_page_tab", {"tab": tabs[0]["tab"]}, "call-focus")
                else:
                    output = final("TABS_OK")
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream"},
                    body=output,
                )

            await side.unroute("**/chat/completions")
            await side.route("**/chat/completions", tabs_handler)
            body = await ask(side, "List the page tabs and focus the first one.", "TABS_OK")
            results["pageTabs"] = {
                "status": "passed" if "TABS_OK" in body else "failed",
                "modelTurns": tabs_turns["n"],
                "body": body[-1200:],
            }

            # Paging: bind the long page and read two segments.
            await bind(side, {"path": "/paging"})
            paging_turns = {"n": 0, "cursor": None}

            async def paging_handler(route: Any) -> None:
                paging_turns["n"] += 1
                n = paging_turns["n"]
                body = route.request.post_data or ""
                if n == 1:
                    output = tool_call("perceive_page", {}, "call-perceive-1")
                elif n == 2:
                    data = tool_data(body) or {}
                    segment = data.get("segment") if isinstance(data, dict) else None
                    cursor = segment.get("cursor") if isinstance(segment, dict) else None
                    paging_turns["cursor"] = cursor
                    output = (
                        tool_call("perceive_page", {"cursor": cursor}, "call-perceive-2")
                        if cursor
                        else final("PAGING_NO_CURSOR")
                    )
                else:
                    output = final("PAGING_OK")
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream"},
                    body=output,
                )

            await side.unroute("**/chat/completions")
            await side.route("**/chat/completions", paging_handler)
            body = await ask(side, "Read the page in segments.", "PAGING_OK")
            results["paging"] = {
                "status": "passed" if "PAGING_OK" in body and paging_turns["cursor"] else "failed",
                "modelTurns": paging_turns["n"],
                "cursorObserved": bool(paging_turns["cursor"]),
                "body": body[-1200:],
            }

            # Page image: perceive the canvas, capture it through the real host.
            await bind(side, {"path": "/image"})
            image_turns = {"n": 0, "ref": None, "captured": False}

            def image_ref(value: Any) -> str | None:
                if isinstance(value, dict):
                    ref = value.get("ref")
                    if isinstance(ref, str) and value.get("capturable") is True:
                        return ref
                    for child in value.values():
                        found = image_ref(child)
                        if found is not None:
                            return found
                elif isinstance(value, list):
                    for child in value:
                        found = image_ref(child)
                        if found is not None:
                            return found
                return None

            async def image_handler(route: Any) -> None:
                image_turns["n"] += 1
                n = image_turns["n"]
                body = route.request.post_data or ""
                if n == 1:
                    output = tool_call("perceive_page", {"images": True}, "call-perceive-image")
                elif n == 2:
                    data = tool_data(body) or {}
                    (OUT / "image-perception.json").write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    image_turns["ref"] = image_ref(data)
                    output = (
                        tool_call(
                            "capture_page_image",
                            {"ref": image_turns["ref"], "note": "R4 extension image"},
                            "call-capture-image",
                        )
                        if image_turns["ref"]
                        else final("IMAGE_NO_REF")
                    )
                else:
                    image_turns["captured"] = '"artifact"' in body or '"ok":true' in body
                    output = final("IMAGE_OK" if image_turns["captured"] else "IMAGE_NO_RESULT")
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream"},
                    body=output,
                )

            await side.unroute("**/chat/completions")
            await side.route("**/chat/completions", image_handler)
            body = await ask(side, "Read and capture the canvas image.", "IMAGE_OK")
            results["pageImage"] = {
                "status": "passed"
                if "IMAGE_OK" in body and image_turns["ref"] and image_turns["captured"]
                else "failed",
                "modelTurns": image_turns["n"],
                "refObserved": bool(image_turns["ref"]),
                "captured": image_turns["captured"],
                "body": body[-1200:],
            }

            # PDF attachment over the extension side panel.
            await bind(side, {"path": "/main"})
            pdf_turns = {"n": 0, "attachment": None, "textObserved": False}

            async def pdf_handler(route: Any) -> None:
                pdf_turns["n"] += 1
                n = pdf_turns["n"]
                body = route.request.post_data or ""
                if n == 1:
                    match = re.search(r"bulletin\.pdf.*?id:\s*([A-Za-z0-9_-]+)", body, re.DOTALL)
                    pdf_turns["attachment"] = match.group(1) if match else None
                    output = (
                        tool_call(
                            "read_document",
                            {"id": pdf_turns["attachment"]},
                            "call-read-pdf",
                        )
                        if pdf_turns["attachment"]
                        else final("PDF_NO_ATTACHMENT")
                    )
                else:
                    pdf_turns["textObserved"] = "Octop embedded viewer" in body
                    output = final("PDF_OK" if pdf_turns["textObserved"] else "PDF_NO_TEXT")
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream"},
                    body=output,
                )

            await side.unroute("**/chat/completions")
            await side.route("**/chat/completions", pdf_handler)
            attach = side.get_by_test_id("composer-attach")
            async with side.expect_file_chooser() as chooser_info:
                await attach.click()
            await (await chooser_info.value).set_files(str(PDF))
            await side.wait_for_timeout(1500)
            body = await ask(side, "Read the attached PDF.", "PDF_OK")
            results["pdf"] = {
                "status": "passed"
                if "PDF_OK" in body
                and pdf_turns["attachment"] is not None
                and pdf_turns["textObserved"]
                else "failed",
                "modelTurns": pdf_turns["n"],
                "attachmentId": pdf_turns["attachment"],
                "textObserved": pdf_turns["textObserved"],
                "body": body[-1200:],
            }

            # Downloads: trigger a real browser download, then list/read it.
            main = pages["main"]
            await bind(side, {"path": "/main"})
            await main.evaluate("document.getElementById('dl').click()")
            await side.wait_for_timeout(1500)
            download_turns = {"n": 0, "id": None, "textObserved": False}

            async def download_handler(route: Any) -> None:
                download_turns["n"] += 1
                n = download_turns["n"]
                body = route.request.post_data or ""
                if n == 1:
                    output = tool_call("list_downloaded_files", {}, "call-list-downloads")
                elif n == 2:
                    data = tool_data(body) or {}
                    files = data.get("files") if isinstance(data, dict) else None
                    if not files:
                        output = final("DOWNLOAD_NO_FILE")
                    else:
                        download_turns["id"] = files[0]["id"]
                        output = tool_call(
                            "read_downloaded_file",
                            {"id": files[0]["id"]},
                            "call-read-download",
                        )
                else:
                    download_turns["textObserved"] = "HOST_DOWNLOAD_OK" in body
                    output = final(
                        "DOWNLOAD_OK" if download_turns["textObserved"] else "DOWNLOAD_NO_TEXT"
                    )
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream"},
                    body=output,
                )

            await side.unroute("**/chat/completions")
            await side.route("**/chat/completions", download_handler)
            body = await ask(side, "List downloads and read report.txt.", "DOWNLOAD_OK")
            results["downloads"] = {
                "status": "passed"
                if "DOWNLOAD_OK" in body
                and download_turns["id"] is not None
                and download_turns["textObserved"]
                else "failed",
                "modelTurns": download_turns["n"],
                "downloadId": download_turns["id"],
                "textObserved": download_turns["textObserved"],
                "body": body[-1200:],
            }
            return {
                "status": all(item["status"] == "passed" for item in results.values()),
                "extensionId": extension_id,
                "serviceWorkers": [worker.url for worker in context.service_workers],
                "capabilities": results,
                "pageErrors": errors,
            }
        except Exception as error:
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "capabilities": results,
                "pageErrors": errors,
            }
        finally:
            await context.close()
            server.shutdown()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "resources.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
