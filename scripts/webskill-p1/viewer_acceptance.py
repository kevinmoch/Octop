"""Exercise real artifact delivery into the isolated embedded viewer."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import traceback
import unicodedata
from pathlib import Path
from typing import Any

from docx import Document
from octop_browser.cdp.launcher import find_chrome
from openpyxl import load_workbook
from playwright.async_api import async_playwright
from pptx import Presentation
from pypdf import PdfReader
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18088")
SDK_ROOT = ROOT.parent / "web-skill-sdk"
CASE = os.environ.get("WEBSKILL_DOCUMENT_CASE", "screen")
if CASE not in {"screen", "bulletin", "slides", "spreadsheet"}:
    raise ValueError(f"Unknown document acceptance case: {CASE}")
SKILL_NAME = f"authored-{CASE}"
SKILL_ROOT = SDK_ROOT / "examples/browser-extension/skills/builtin" / SKILL_NAME
LLM_URL = "https://webskill-viewer-model.test/v1"
MARKER = "Octop embedded viewer fixture"


def model_frame(delta: dict[str, Any]) -> str:
    return f"data: {json.dumps({'choices': [{'delta': delta}]})}"


def tool_call(name: str, arguments: dict[str, Any]) -> str:
    return model_frame(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"call-{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        }
    )


async def run() -> dict[str, Any]:
    errors: list[str] = []
    console_errors: list[str] = []
    model_turns = 0
    html = (
        '<div class="viewer-fixture" data-viewer-chrome="hidden">'
        f'<h1 id="subject">{MARKER}</h1><p>实时运行总览</p>'
        '<div data-webskill-component="Metric" '
        'data-webskill-props=\'{"label":"Active sessions","value":42}\'></div></div>'
    )
    css = (
        ".viewer-fixture { height: 100vh; overflow: hidden; min-height: 0; } "
        "#subject { font-size: 23px; }"
    )
    publish_arguments: dict[str, Any] = {
        "html": html,
        "css": css,
        "dataSource": "fixture:local",
    }
    if CASE == "bulletin":
        publish_arguments = {
            "doc": {
                "docType": "plain",
                "title": MARKER,
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "Generated in the isolated Octop viewer.",
                    }
                ],
            },
            "dataSource": "fixture:local",
        }
    elif CASE == "slides":
        publish_arguments = {
            "deck": {
                "title": MARKER,
                "theme": "light",
                "slides": [
                    {"layout": "cover", "title": MARKER, "body": []},
                    {
                        "layout": "single",
                        "title": "Delivery",
                        "body": [
                            {
                                "type": "metrics",
                                "items": [
                                    {"label": "Completed", "value": 42},
                                    {"label": "Open", "value": 7},
                                ],
                            }
                        ],
                        "takeaway": "Delivery metrics come from the reviewed fixture.",
                    },
                    {
                        "layout": "closing",
                        "title": "Next steps",
                        "body": [
                            {
                                "type": "bullets",
                                "items": [
                                    "Review the report",
                                    "Confirm ownership",
                                    "Track the next milestone",
                                ],
                            }
                        ],
                        "takeaway": "Use the reviewed actions for the next iteration.",
                    },
                ],
            },
            "dataSource": "fixture:local",
        }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        context = await browser.new_context(locale="zh-CN")
        await context.add_init_script(
            """(config => {
              if (!['/webskill/', '/webskill/index.html'].includes(location.pathname)) return;
              const key = 'octop.webskill.runtime.1';
              const current = JSON.parse(localStorage.getItem(key) || '{}');
              current.llm = config.llm;
              current.appearance = { locale: 'zh', theme: 'light' };
              current.documentSurface = { enabled: true };
              current.sandbox = {
                ...(current.sandbox || {}),
                capabilities: {
                  ...(current.sandbox?.capabilities || {}),
                  writeArtifact: true,
                  confirm: true
                }
              };
              localStorage.setItem(key, JSON.stringify(current));
              window.addEventListener('message', event => {
                if (event.data?.type === 'webskill:viewer-ready') {
                  window.__viewerTestChannel = event.data.channelId;
                }
              }, true);
            })({
              llm: {
                entries: [{
                  id: 'scripted', label: 'Scripted', provider: 'openai-compatible',
                  model: 'scripted-agent', baseUrl: 'https://webskill-viewer-model.test/v1',
                  apiKey: 'sk-e2e', capabilities: { tools: true, image: false }
                }],
                defaultId: 'scripted'
              }
            })"""
        )
        page = await context.new_page()
        def on_page_error(error: Any) -> None:
            message = safe_error(error)
            if "WEBSKILL_TEST_PRINT_CAPTURE" not in message:
                errors.append(message)

        page.on("pageerror", on_page_error)
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        await page.route(f"{LLM_URL}/models", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"data":[]}'
        ))

        async def model_reply(route: Any) -> None:
            nonlocal model_turns
            turn = model_turns
            model_turns += 1
            call: str | None = None
            if CASE == "spreadsheet" and turn == 0:
                call = tool_call(
                    "export_spreadsheet",
                    {
                        "fileName": "octop-report.xlsx",
                        "spec": {
                            "sheets": [
                                {
                                    "name": "Summary",
                                    "columns": ["Metric", "Value"],
                                    "rows": [[MARKER, 42]],
                                }
                            ]
                        },
                    },
                )
            elif CASE != "spreadsheet" and turn == 0:
                call = tool_call("read_skill_file", {"skillName": SKILL_NAME})
            elif CASE != "spreadsheet" and turn == 1:
                call = tool_call(
                    "read_skill_file",
                    {"skillName": SKILL_NAME, "path": "references/authoring.md"},
                )
            elif CASE != "spreadsheet" and turn == 2:
                call = tool_call(f"{SKILL_NAME}__publish", publish_arguments)
            body = (
                f"{call}\n\ndata: [DONE]\n\n"
                if call is not None
                else f"{model_frame({'content': 'ARTIFACT_READY'})}\n\ndata: [DONE]\n\n"
            )
            await route.fulfill(
                status=200, headers={"content-type": "text/event-stream"}, body=body
            )

        await page.route(f"{LLM_URL}/chat/completions", model_reply)
        try:
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            if await page.locator("input").count() >= 2:
                await page.locator("input").nth(0).fill("admin")
                await page.locator("input").nth(1).fill("TestPass12")
                thumb = page.get_by_test_id("slide-captcha-thumb")
                box = await thumb.bounding_box()
                assert box is not None
                track = await page.get_by_test_id("slide-captcha-track").bounding_box()
                assert track is not None
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.mouse.down()
                await page.mouse.move(
                    track["x"] + track["width"],
                    box["y"] + box["height"] / 2,
                    steps=12,
                )
                await page.mouse.up()
                await page.get_by_role("button", name=re.compile("登")).click()
                await page.wait_for_url(re.compile(r"/chat/"))
            dismiss = page.get_by_role("button", name="稍后再说")
            if await dismiss.is_visible():
                await dismiss.click()
            menu = page.get_by_role("button", name="浏览器", exact=True)
            if not await menu.is_visible():
                await page.get_by_role("button", name="控制", exact=True).click()
            await menu.click()
            await page.wait_for_url(f"{BASE_URL}/browser")
            await page.get_by_role("button", name="启动浏览器").click()
            frame_element = page.locator('iframe[title="WebSkill"]')
            await frame_element.wait_for(state="attached", timeout=30000)
            await page.wait_for_timeout(1000)
            frame = next(
                (item for item in page.frames if item.url.endswith("/webskill/")), None
            )
            assert frame is not None
            if CASE != "spreadsheet":
                files = {
                    name: (SKILL_ROOT / name).read_text(encoding="utf-8")
                    for name in (
                        "SKILL.md",
                        "scripts/publish.js",
                        "references/authoring.md",
                    )
                }
                await frame.evaluate(
                    """async ({ name, files }) => {
                      const root = await navigator.storage.getDirectory();
                      const app = await root.getDirectoryHandle('octop-webskill-user-1', { create: true });
                      const skills = await app.getDirectoryHandle('skills', { create: true });
                      const skillDir = await skills.getDirectoryHandle(name, { create: true });
                      for (const [path, content] of Object.entries(files)) {
                        const parts = path.split('/');
                        let directory = skillDir;
                        for (const part of parts.slice(0, -1)) {
                          directory = await directory.getDirectoryHandle(part, { create: true });
                        }
                        const file = await directory.getFileHandle(parts.at(-1), { create: true });
                        const writable = await file.createWritable();
                        await writable.write(content);
                        await writable.close();
                      }
                    }""",
                    {"name": SKILL_NAME, "files": files},
                )
                await frame.goto(frame.url)
            composer = frame.locator("textarea:visible").last
            await composer.fill(f"Produce the {CASE} fixture artifact.")
            await composer.press("Enter")
            if CASE == "spreadsheet":
                await frame.get_by_text("ARTIFACT_READY", exact=True).wait_for(
                    timeout=30000
                )
                artifacts = await frame.evaluate(
                    """async () => {
                      const root = await navigator.storage.getDirectory();
                      const app = await root.getDirectoryHandle('octop-webskill-user-1');
                      const found = [];
                      async function walk(directory, prefix, depth) {
                        if (depth > 8) return;
                        for await (const [name, entry] of directory.entries()) {
                          const path = prefix ? `${prefix}/${name}` : name;
                          if (entry.kind === 'directory') await walk(entry, path, depth + 1);
                          else if (name.endsWith('.xlsx')) {
                            const file = await entry.getFile();
                            found.push({ path, bytes: Array.from(new Uint8Array(await file.arrayBuffer())) });
                          }
                        }
                      }
                      await walk(app, '', 0);
                      return found;
                    }"""
                )
                assert len(artifacts) == 1, artifacts
                path = OUT / "octop-report.xlsx"
                path.write_bytes(bytes(artifacts[0]["bytes"]))
                workbook = load_workbook(io.BytesIO(path.read_bytes()), read_only=True)
                sheet = workbook["Summary"]
                values = list(sheet.iter_rows(values_only=True))
                workbook.close()
                assert values == [("Metric", "Value"), (MARKER, 42)], values
                return {
                    "status": "passed",
                    "case": CASE,
                    "modelMode": "scripted",
                    "modelTurns": model_turns,
                    "artifactCreated": True,
                    "reopenedWith": "openpyxl",
                    "contentVerified": True,
                    "pageErrors": errors,
                    "consoleErrors": console_errors,
                }
            open_document = frame.get_by_role("button", name="打开文档", exact=True)
            await open_document.wait_for(timeout=30000)
            await open_document.click()
            allow = frame.get_by_role("button", name="允许", exact=True)
            await allow.wait_for(timeout=20000)
            async with page.expect_event(
                "framenavigated",
                lambda item: item.url.startswith(f"{BASE_URL}/webskill/viewer/"),
                timeout=20000,
            ) as navigated:
                await allow.click()
            viewer = await navigated.value
            viewer_element = frame.get_by_test_id("document-viewer")
            await viewer_element.wait_for(state="attached", timeout=20000)
            await viewer.get_by_text(MARKER, exact=True).wait_for(timeout=20000)
            isolation = await viewer.evaluate(
                """() => {
                  let storage = 'available';
                  let parent = 'available';
                  try { localStorage.setItem('viewer-probe', 'x'); } catch (error) { storage = error.name; }
                  try { void window.parent.document; } catch (error) { parent = error.name; }
                  return {
                    origin: globalThis.origin,
                    locationOrigin: location.origin,
                    storage,
                    parent,
                    inlineScriptExecuted: window.__viewer_inline_script === true,
                    fontSize: document.getElementById('subject')
                      ? getComputedStyle(document.getElementById('subject')).fontSize
                      : ''
                  };
                }"""
            )
            assert isolation["origin"] == "null", isolation
            assert isolation["storage"] == "SecurityError", isolation
            assert isolation["parent"] == "SecurityError", isolation
            if CASE == "screen":
                assert isolation["fontSize"] == "23px", isolation
                await viewer.get_by_text("实时运行总览", exact=True).wait_for()
                await viewer.get_by_text("Active sessions", exact=True).wait_for()
                assert not await viewer.get_by_test_id("viewer-export").is_visible()
            elif CASE == "slides":
                await viewer.locator(".deck.reveal .slides section").first.wait_for()
            export_format: str | None = None
            if CASE in {"bulletin", "slides"}:
                export = viewer.get_by_test_id("viewer-export")
                await export.wait_for(state="visible", timeout=20000)
                async with page.expect_event("download", timeout=30000) as downloaded:
                    await export.click()
                download = await downloaded.value
                export_format = "docx" if CASE == "bulletin" else "pptx"
                assert download.suggested_filename.endswith(f".{export_format}")
                artifact = OUT / download.suggested_filename
                await download.save_as(str(artifact))
                if CASE == "bulletin":
                    document = Document(artifact)
                    text = "\n".join(
                        paragraph.text for paragraph in document.paragraphs
                    )
                else:
                    presentation = Presentation(artifact)
                    text = "\n".join(
                        shape.text
                        for slide in presentation.slides
                        for shape in slide.shapes
                        if hasattr(shape, "text")
                    )
                assert MARKER in text, text
                print(f"[{CASE}] export downloaded and reopened", flush=True)
                print_pages = 0
                if CASE == "slides":
                    await viewer.evaluate(
                        "() => { window.print = () => { throw new Error('WEBSKILL_TEST_PRINT_CAPTURE'); }; }"
                    )
                    await viewer.get_by_test_id("viewer-print").click()
                    await viewer.locator(".pdf-page").first.wait_for(timeout=10000)
                    print_pages = await viewer.locator(".pdf-page").count()
                    assert print_pages == 3, print_pages
                elif CASE == "bulletin":
                    await viewer.get_by_test_id("viewer-print").click()
                    print_cdp = await context.new_cdp_session(page)
                    printed = await print_cdp.send(
                        "Page.printToPDF",
                        {"printBackground": True, "preferCSSPageSize": True},
                    )
                    pdf = OUT / "bulletin.pdf"
                    pdf.write_bytes(base64.b64decode(printed["data"]))
                    reader = PdfReader(pdf)
                    pdf_text = " ".join(
                        (page.extract_text() or "") for page in reader.pages
                    )
                    normalized_pdf = re.sub(
                        r"\s+", " ", unicodedata.normalize("NFKC", pdf_text)
                    )
                    assert MARKER in normalized_pdf, normalized_pdf[:1000]
            channel = await frame.evaluate("() => window.__viewerTestChannel")
            assert isinstance(channel, str) and channel
            forged = "<h1>FORGED_VIEWER_CONTENT</h1>"
            await viewer.evaluate(
                """({ channel, html }) => window.postMessage({
                  type: 'webskill:viewer-document', channelId: channel,
                  document: { html }
                }, '*')""",
                {"channel": channel, "html": forged},
            )
            await page.evaluate(
                """({ channel, html }) => {
                  const host = document.querySelector('iframe[title="WebSkill"]');
                  const target = host.contentWindow.document.querySelector(
                    '[data-testid="document-viewer"]'
                  ).contentWindow;
                  target.postMessage({
                    type: 'webskill:viewer-document', channelId: channel,
                    document: { html }
                  }, '*');
                }""",
                {"channel": channel, "html": forged},
            )
            await frame.evaluate(
                """({ html }) => document.querySelector(
                  '[data-testid="document-viewer"]'
                ).contentWindow.postMessage({
                  type: 'webskill:viewer-document', channelId: 'wrong-channel',
                  document: { html }
                }, '*')""",
                {"html": forged},
            )
            await viewer.get_by_text(MARKER, exact=True).wait_for()
            assert await viewer.get_by_text("FORGED_VIEWER_CONTENT").count() == 0
            response = await page.request.get(f"{BASE_URL}/webskill/viewer/")
            policy = response.headers.get("content-security-policy", "")
            assert response.ok
            assert "sandbox allow-scripts allow-modals allow-downloads" in policy
            assert "allow-same-origin" not in policy
            assert "connect-src 'none'" in policy
            assert f"script-src {BASE_URL}" in policy
            await frame.get_by_role("button", name="关闭文档", exact=True).click()
            remaining = await frame.get_by_test_id("document-viewer").count()
            assert remaining == 0, f"viewer DOM remained after close: {remaining}"
            assert model_turns == 4
            assert not errors, errors
            return {
                "status": "passed",
                "case": CASE,
                "modelMode": "scripted",
                "modelTurns": model_turns,
                "artifactCreated": True,
                "viewerOpened": True,
                "httpSandbox": True,
                "opaqueOrigin": True,
                "storageBlocked": True,
                "parentAccessBlocked": True,
                **({"styleApplied": True} if CASE == "screen" else {}),
                **(
                    {
                        "exportFormat": export_format,
                        "contentVerified": True,
                        "reopenedWith": "python-docx"
                        if CASE == "bulletin"
                        else "python-pptx",
                    }
                    if export_format is not None
                    else {}
                ),
                **(
                    {"pdfVerified": True, "pdfReopenedWith": "pypdf"}
                    if CASE == "bulletin"
                    else {}
                ),
                **(
                    {"printPagesVerified": print_pages}
                    if CASE == "slides"
                    else {}
                ),
                "wrongSourceRejected": True,
                "wrongChannelRejected": True,
                "closeRemovedDom": True,
                "pageErrors": errors,
                "consoleErrors": console_errors,
            }
        except Exception as error:
            try:
                details = frame.get_by_text("运行细节", exact=True)
                if await details.count():
                    await details.first.click()
                    await page.wait_for_timeout(100)
            except Exception:
                pass
            await page.screenshot(
                path=str(OUT / f"viewer-{CASE}-failure.png"), full_page=True
            )
            current_viewer = next(
                (item for item in page.frames if "/webskill/viewer/" in item.url),
                None,
            )
            chrome = (
                await current_viewer.evaluate(
                    """() => {
                      const bar = document.getElementById('viewer-chrome');
                      const button = document.getElementById('viewer-export');
                      return {
                        hidden: bar.hidden, display: getComputedStyle(bar).display,
                        rect: bar.getBoundingClientRect().toJSON(),
                        buttonHidden: button.hidden,
                        buttonRect: button.getBoundingClientRect().toJSON(),
                        viewport: [innerWidth, innerHeight],
                        printCalled: window.__printCalled || 0,
                        htmlClass: document.documentElement.className,
                        pdfPages: document.querySelectorAll('.pdf-page').length,
                        bodySize: [document.body.style.width, document.body.style.height]
                      };
                    }"""
                )
                if current_viewer is not None
                else None
            )
            return {
                "status": "failed",
                "case": CASE,
                "viewerChrome": chrome,
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "traceback": traceback.format_exc()[-3000:],
                "pageErrors": errors,
                "consoleErrors": console_errors,
                "modelTurns": model_turns,
                "bodyText": (await page.locator("body").inner_text())[:3000],
                "frameBodies": {
                    item.url: (await item.locator("body").inner_text())[:5000]
                    for item in page.frames
                    if not item.url.startswith("chrome-error:")
                },
            }
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    result_name = "viewer" if CASE == "screen" else f"document-{CASE}"
    (OUT / f"ui-result-{result_name}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
