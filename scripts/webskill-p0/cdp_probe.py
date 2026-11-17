from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts/webskill-p0"
OUT.mkdir(parents=True, exist_ok=True)
PROFILE = f"octop-p0-{uuid.uuid4().hex[:10]}"
os.environ["BROWSER_USE_PROFILES_DIR"] = str(OUT / "profiles")
os.environ["BROWSER_RECORD_REPLAY_DIR"] = str(OUT / "records")
os.environ["BROWSER_USE_CDP_PORT_START"] = str(30000 + os.getpid() % 10000)
os.environ["BROWSER_USE_SCREENSHOTS_DIR"] = str(OUT / "screenshots")

from octop_browser.actions.interact import click as cdp_click  # noqa: E402
from octop_browser.cdp.client import CDPClient  # noqa: E402
from octop_browser.cdp.launcher import find_chrome  # noqa: E402
from octop_browser.dom.refs import RefCache  # noqa: E402
from octop_browser.profile import ProfileManager  # noqa: E402
from octop_browser.record.daemon import is_running, send_request, start_daemon_process  # noqa: E402
from octop_browser.record.models import RecordingManifest, StepsDocument  # noqa: E402
from octop_browser.record.replay import ReplayRunner  # noqa: E402
from octop_browser.record.skill_generator import SkillGenerator  # noqa: E402
from octop_browser.record.store import RecordingStore  # noqa: E402
from octop_browser.session import BrowserSession  # noqa: E402
from octop_browser.settings import OctopSettings  # noqa: E402
from octop_harness.builtin.tools.browser_use import browser_use  # noqa: E402
from oopif_probe import attach_read_click  # noqa: E402

RESULTS: list[dict[str, Any]] = []
WRITE_LOCK = threading.Lock()
WRITER_READY_BARRIER = threading.Barrier(2)
WRITE_BARRIER = threading.Barrier(2)
WRITE_STARTS: list[float] = []
WRITE_BARRIER_COMPLETED = False
BROWSER_PRODUCT = "unknown"


def safe_error(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)[:500]}


def record(
    probe: str,
    status: str,
    actual: str,
    ac: list[str],
    **details: Any,
) -> None:
    entry = {
        "probe": probe,
        "status": status,
        "acStatus": "failed"
        if status == "failed"
        else "gap"
        if status == "unsupported"
        else "not-assessed",
        "actual": actual,
        "commands": [".venv/bin/python scripts/webskill-p0/cdp_probe.py"],
        "ac": ac,
        "environment": f"Chrome {BROWSER_PRODUCT}; octop-browser {version('octop-browser')}; octop-harness {version('octop-harness')}; SDK 0.29.0; Octop develop@a01c397e",
        "evidence": [".artifacts/webskill-p0/cdp-probes.json"],
        **details,
    }
    RESULTS.append(entry)
    print(f"[{status}] {probe}: {actual}", flush=True)


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        global WRITE_BARRIER_COMPLETED
        route = urlparse(self.path).path
        if route in {"/writer-ready", "/write"}:
            if route == "/write":
                with WRITE_LOCK:
                    WRITE_STARTS.append(time.monotonic())
            barrier = WRITER_READY_BARRIER if route == "/writer-ready" else WRITE_BARRIER
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                if route == "/write":
                    WRITE_BARRIER_COMPLETED = False
            else:
                if route == "/write":
                    WRITE_BARRIER_COMPLETED = True
            self.respond_json({"accepted": True})
            return
        pages = {
            "/main": """
<!doctype html><html><body>
<button id="original" onclick="window.originalClicks=(window.originalClicks||0)+1">Original</button>
<button id="insert" onclick="const b=document.createElement('button');b.id='original';b.onclick=()=>window.insertedClicks=(window.insertedClicks||0)+1;b.textContent='Inserted';document.body.prepend(b)">Insert</button>
<button id="process-write" onclick="window.processWrites=(window.processWrites||0)+1">Process write</button>
<button id="record-action" onclick="document.getElementById('record-result').textContent='Recorded action: 1'">Record demo action</button>
<span id="record-result"></span>
<a id="navigate" href="/next">Navigate</a>
<a id="blank" href="/child" target="_blank">Blank target</a>
<button id="window-open" onclick="window.open('/child','_blank')">Window open</button>
<div id="clock">0</div>
<iframe id="same-frame" src="/frame"></iframe>
<iframe id="cross-frame" src="http://127.0.0.1:CROSS_PORT/frame?cross=1"></iframe>
<script>window.clock=0;setInterval(()=>document.getElementById('clock').textContent=String(++window.clock),250)</script>
</body></html>
""",
            "/frame": """
<!doctype html><html><body><span id="frame-marker">frame-content</span>
<button id="frame-action" onpointerdown="window.pointerDowns=(window.pointerDowns||0)+1" onclick="window.frameClicks=(window.frameClicks||0)+1">Frame action</button>
<a id="frame-nav" href="/frame-next">Frame navigation</a></body></html>
""",
            "/frame-next": "<!doctype html><html><body><span id='next-frame'>next-frame</span><button id='replacement'>Replacement</button></body></html>",
            "/next": "<!doctype html><html><body><h1>Next document</h1><button id='new-button'>New</button></body></html>",
            "/child": "<!doctype html><html><body><h1>Child target</h1><button id='child-action' onclick='this.dataset.clicked=\"true\"'>Child action</button></body></html>",
        }
        body = pages.get(route)
        if body is None:
            self.send_error(404)
            return
        body = body.replace("CROSS_PORT", str(self.server.cross_origin_port))
        self.respond_html(body)

    def do_POST(self) -> None:
        self.do_GET()

    def respond_html(self, body: str) -> None:
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def respond_json(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_servers() -> tuple[ThreadingHTTPServer, ThreadingHTTPServer]:
    first = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    second = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    first.cross_origin_port = second.server_port
    second.cross_origin_port = first.server_port
    threading.Thread(target=first.serve_forever, daemon=True).start()
    threading.Thread(target=second.serve_forever, daemon=True).start()
    return first, second


async def structured(session: BrowserSession) -> dict[str, Any]:
    result = await session.dom_tree(level="structured")
    if not result.success or not isinstance(result.content, str):
        raise RuntimeError(result.error or "DOM snapshot failed")
    return json.loads(result.content)


def find_ref(dom: dict[str, Any], element_id: str) -> str | None:
    for key in ("elements", "children", "contentDocument"):
        value = dom.get(key)
        if not isinstance(value, list):
            if isinstance(value, dict):
                nested = find_ref(value, element_id)
                if nested is not None:
                    return nested
            continue
        for element in value:
            if not isinstance(element, dict):
                continue
            if element.get("id") == element_id and element.get("ref"):
                return str(element["ref"])
            nested = find_ref(element, element_id)
            if nested is not None:
                return nested
    return None


def raw_nodes(root: dict[str, Any]) -> list[dict[str, Any]]:
    result = [root]
    for child in root.get("children", []):
        result.extend(raw_nodes(child))
    content = root.get("contentDocument")
    if isinstance(content, dict):
        result.extend(raw_nodes(content))
    return result


def raw_node_by_id(root: dict[str, Any], element_id: str) -> dict[str, Any] | None:
    return next(
        (node for node in raw_nodes(root) if element_id in node.get("attributes", [])),
        None,
    )


async def page_value(session: BrowserSession, expression: str) -> Any:
    result = await session.eval_js(expression)
    if not result.success:
        raise RuntimeError(result.error or "JavaScript probe failed")
    if isinstance(result.content, str):
        try:
            return json.loads(result.content)
        except json.JSONDecodeError:
            return result.content
    return result.content


async def targets(port: int) -> list[dict[str, Any]]:
    import aiohttp

    async with (
        aiohttp.ClientSession() as http,
        http.get(f"http://127.0.0.1:{port}/json") as response,
    ):
        return [t for t in await response.json(content_type=None) if t.get("type") == "page"]


async def connected_client(port: int, target_id: str) -> CDPClient:
    match = next(t for t in await targets(port) if t["id"] == target_id)
    client = CDPClient(timeout=10)
    await client.connect(str(match["webSocketDebuggerUrl"]))
    await client.enable_domain("Page")
    await client.enable_domain("DOM")
    await client.enable_domain("Runtime")
    return client


async def target_visibility(client: CDPClient) -> str:
    result = await client.send(
        "Runtime.evaluate",
        {"expression": "document.visibilityState", "returnByValue": True},
    )
    return str(result.get("result", {}).get("value") or "unknown")


async def observe_target_visibility(
    port: int, session: BrowserSession, target_id: str
) -> dict[str, str]:
    client = await connected_client(port, target_id)
    try:
        return {
            "newTargetVisibility": await target_visibility(client),
            "previousTargetVisibility": await target_visibility(session._internal.client),
        }
    finally:
        await client.close()


async def wait_for_target(port: int, prior: set[str]) -> str:
    for _ in range(40):
        current = await targets(port)
        added = [str(t["id"]) for t in current if str(t["id"]) not in prior]
        if added:
            return added[0]
        await asyncio.sleep(0.1)
    raise TimeoutError("The click did not create a new target")


async def attempt_click(session: BrowserSession, ref: str) -> Any:
    try:
        return await session.click(ref=ref)
    except Exception as error:
        return SimpleNamespace(success=False, error=str(error))


async def writer_child(profile: str, url: str) -> int:
    settings = OctopSettings(cdp_ws_url=os.environ["OCTOP_P0_TARGET_WS"])
    session = await BrowserSession.create(profile=profile, mode="headless", settings=settings)
    try:
        current = await page_value(session, "location.href")
        if current != url:
            await session.navigate(url)
        dom = await structured(session)
        ref = find_ref(dom, "process-write")
        if not ref:
            return 2
        writer_arg = json.dumps(os.environ["OCTOP_P0_WRITER_ID"])
        before = await page_value(session, "window.processWrites||0")
        document_started_at = await page_value(session, "performance.timeOrigin")
        ready = await session.eval_js("fetch('/writer-ready').then(() => true)")
        if not ready.success:
            return 4
        result = await session.click(ref=ref)
        count = before
        for _ in range(100):
            count = await page_value(session, "window.processWrites||0")
            if count > before:
                break
            await asyncio.sleep(0.05)
        write_completed = (
            await page_value(
                session,
                "fetch('/write?writer=' + "
                + writer_arg
                + ", {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'}).then(r => r.ok).catch(() => false)",
            )
            if result.success
            else False
        )
        print(
            json.dumps(
                {
                    "clickSuccess": result.success,
                    "beforeWrites": before,
                    "processWrites": count,
                    "documentStartedAt": document_started_at,
                    "writeCompleted": write_completed,
                }
            )
        )
        return 0 if result.success and write_completed is True else 3
    finally:
        await session.close()


def compare_source(source: Path, installed: Path) -> dict[str, Any]:
    source_files = {str(f.relative_to(source)): f for f in source.rglob("*.py")}
    installed_files = {str(f.relative_to(installed)): f for f in installed.rglob("*.py")}
    return {
        "filesCompared": len(source_files),
        "differences": sorted(
            path
            for path in source_files.keys() & installed_files.keys()
            if source_files[path].read_bytes() != installed_files[path].read_bytes()
        ),
        "missingFiles": sorted(source_files.keys() - installed_files.keys()),
        "additionalFiles": sorted(installed_files.keys() - source_files.keys()),
        "fileManifestSha256": hashlib.sha256(
            json.dumps(
                {
                    path: hashlib.sha256(source_files[path].read_bytes()).hexdigest()
                    for path in sorted(source_files)
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }


def start_writer(url: str, writer_id: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--writer", PROFILE, url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "OCTOP_P0_WRITER_ID": writer_id},
    )


async def harness_and_source() -> None:
    result = await browser_use.ainvoke(
        {
            "action": "eval_js",
            "profile": PROFILE,
            "expression": "window.harnessP0 = 'observed'; true",
        }
    )
    payload = json.loads(result) if isinstance(result, str) else {}
    record(
        "harness-browser-use-eval-js",
        "supported" if payload.get("success") is True else "failed",
        "The installed octop-harness tool invoked octop-browser eval_js in the isolated shared profile; no WebSkill exposure is implied",
        ["AC-60"],
        harnessVersion=version("octop-harness"),
        action=payload.get("action"),
        success=payload.get("success"),
        permissionGate="no Octop user grant or cross-process write lease in this path",
        acStatus="gap",
    )
    source = OUT / "sources/octop-harness/src/octop_harness"
    installed = Path(__import__("octop_harness").__file__ or "").parent
    if not source.is_dir():
        record(
            "harness-source-match",
            "failed",
            "Clone the v1.0.9 tag before running this probe",
            ["AC-60"],
        )
        return
    comparison = await asyncio.to_thread(compare_source, source, installed)
    commit = (
        await asyncio.to_thread(
            subprocess.check_output,
            ["git", "-C", str(source.parent.parent), "rev-parse", "HEAD"],
            text=True,
        )
    ).strip()
    commit_matches = commit == "65b54561f39327b8d6a144a377b00230002cebad"
    source_matches = (
        comparison["filesCompared"] > 0
        and not comparison["differences"]
        and not comparison["missingFiles"]
        and not comparison["additionalFiles"]
        and commit_matches
    )
    record(
        "harness-source-match",
        "supported" if source_matches else "failed",
        "Compared installed and upstream Python sources in both directions, hashed the source file manifest and asserted the pinned v1.0.9 commit",
        ["AC-60"],
        sourceCommit=commit,
        commitMatches=commit_matches,
        installedVersion=version("octop-harness"),
        **comparison,
    )


async def run() -> None:
    global BROWSER_PRODUCT
    if find_chrome() is None:
        raise RuntimeError("No real Chrome/Chromium binary was found")
    first, second = start_servers()
    origin = f"http://localhost:{first.server_port}"
    manager = ProfileManager(base_dir=OUT / "profiles")
    session = await BrowserSession.create(profile=PROFILE, mode="headless", profile_manager=manager)
    profile = manager.get_or_create(PROFILE)
    port = profile.cdp_port
    main_target = ""
    view_client: CDPClient | None = None
    other_client: CDPClient | None = None
    daemon_pid: int | None = None
    try:
        nav = await session.navigate(f"{origin}/main")
        if not nav.success:
            raise RuntimeError(nav.error or "Could not open fixture")
        await session.wait(text="Original")
        tabs = await targets(port)
        main_target = str(tabs[0]["id"])
        browser_version = await session._internal.client.send("Browser.getVersion")
        BROWSER_PRODUCT = str(browser_version.get("product") or "unknown")
        initial = await structured(session)
        original_ref = find_ref(initial, "original")
        if not original_ref:
            raise RuntimeError("Initial button ref is missing")
        await attempt_click(session, original_ref)
        await page_value(session, "document.getElementById('insert').click()")
        inserted = await attempt_click(session, original_ref)
        counts = await page_value(
            session,
            "({original:window.originalClicks||0,inserted:window.insertedClicks||0})",
        )
        observed = counts
        record(
            "dom-insertion-ref-identity",
            "supported"
            if observed.get("original") == 2 and observed.get("inserted", 0) == 0
            else "unsupported",
            "Clicked the pre-insertion ref after inserting a same-id button before the original, using the production BrowserSession",
            ["AC-01", "AC-02"],
            clickReturnedSuccess=inserted.success,
            counts=observed,
            refStorage="nodeId only",
        )
        await session.click(ref=find_ref(await structured(session), "navigate"))
        await session.wait(text="Next document")
        stale = await attempt_click(session, original_ref)
        record(
            "navigation-invalidates-ref",
            "supported" if not stale.success else "unsupported",
            "Used a pre-navigation ref after a real document navigation",
            ["AC-03"],
            success=stale.success,
            error=stale.error,
        )

        await session.navigate(f"{origin}/main")
        await session.wait(text="Original")
        same_dom = await structured(session)
        same_ref = find_ref(same_dom, "frame-action")
        same_entries = [e for e in same_dom.get("elements", []) if e.get("framePath")]
        if same_ref:
            await session.click(ref=same_ref)
            frame_clicks = await page_value(
                session,
                "document.getElementById('same-frame').contentWindow.frameClicks||0",
            )
            record(
                "same-origin-frame-read-click",
                "supported" if frame_clicks == 1 else "unsupported",
                "Read and clicked the same-origin frame using BrowserSession DOM/ref",
                ["AC-04"],
                frameClicks=frame_clicks,
                frameEntries=len(same_entries),
            )
            nav_ref = find_ref(await structured(session), "frame-nav")
            if nav_ref:
                await session.click(ref=nav_ref)
                await asyncio.sleep(0.5)
                stale_frame = await attempt_click(session, same_ref)
                record(
                    "child-frame-navigation-ref",
                    "supported" if not stale_frame.success else "unsupported",
                    "Reused a child-frame ref after independently navigating that frame",
                    ["AC-06"],
                    success=stale_frame.success,
                    error=stale_frame.error,
                )
        else:
            record(
                "same-origin-frame-read-click",
                "unsupported",
                "The current BrowserSession DOM projection does not expose an actionable same-origin frame ref",
                ["AC-04"],
                frameEntries=len(same_entries),
            )
            record(
                "child-frame-navigation-ref",
                "unsupported",
                "The current BrowserSession has no same-origin frame ref whose navigation invalidation can be exercised; this is a missing capability, not an environment failure",
                ["AC-06"],
            )
        cross_dom = await structured(session)
        cross = [
            e for e in cross_dom.get("elements", []) if "cross-frame" in e.get("framePath", [])
        ]
        raw = await session._internal.client.send("DOM.getDocument", {"depth": -1, "pierce": True})
        same_frame_node = raw_node_by_id(raw["root"], "frame-action")
        cross_frame_node = raw_node_by_id(raw["root"], "cross-frame")
        record(
            "same-origin-cdp-pierce",
            "supported" if same_frame_node is not None else "unsupported",
            "Raw CDP DOM.getDocument with pierce=true was checked separately from the existing BrowserSession projection",
            ["AC-04", "AC-08"],
            frameActionVisible=same_frame_node is not None,
            currentProjectionPierces=find_ref(same_dom, "frame-action") is not None,
        )
        target_info = await session._internal.client.send("Target.getTargets")
        iframe_targets = [
            target
            for target in target_info.get("targetInfos", [])
            if target.get("type") == "iframe" and "/frame?cross=1" in str(target.get("url") or "")
        ]
        current_ref = find_ref(cross_dom, "frame-action")
        record(
            "oopif-projection-and-action",
            "supported" if current_ref is not None else "unsupported",
            "The current single-page CDP DOM projection was checked for an actual OOPIF frame ref, not merely framePath presence; a session-aware target path is required",
            ["AC-05", "AC-07", "AC-08"],
            projectedElements=len(cross),
            actionableRefDetected=current_ref is not None,
            crossFrameContentDocument=isinstance(
                (cross_frame_node or {}).get("contentDocument"), dict
            ),
            independentIframeTarget=len(iframe_targets) > 0,
            sitePair=["localhost", "127.0.0.1"],
            sameOriginContentDocumentPresent=same_frame_node is not None,
        )
        if not iframe_targets:
            record(
                "oopif-attach-execution",
                "failed",
                "The cross-site fixture did not produce an independent iframe target; no attach result is claimed",
                ["AC-05"],
            )
        else:
            try:
                page_ws = next(
                    str(target["webSocketDebuggerUrl"])
                    for target in await targets(port)
                    if target["id"] == main_target
                )
                attached = await attach_read_click(page_ws, str(iframe_targets[0]["targetId"]))
                valid = (
                    attached["actionCompleted"] is True
                    and attached["clickCount"] == 1
                    and attached["pointerDownCount"] == 1
                    and attached["observed"]["marker"] == "frame-content"
                )
                record(
                    "oopif-attach-execution",
                    "supported" if valid else "failed",
                    "Raw CDP attached to a real independent iframe target, pinned its default execution context, read its origin/marker and completed real mouse press/release with click and pointerdown evidence; this is not a BrowserSession capability claim",
                    ["AC-05"],
                    **attached,
                )
            except Exception as error:
                record(
                    "oopif-attach-execution",
                    "failed",
                    "The real OOPIF attach/execution probe failed",
                    ["AC-05"],
                    error=safe_error(error),
                )

        await session.navigate(f"{origin}/main")
        await session.wait(text="Original")
        before = {str(t["id"]) for t in await targets(port)}
        blank_ref = find_ref(await structured(session), "blank")
        if blank_ref:
            await session.click(ref=blank_ref)
            new_target = await wait_for_target(port, before)
            current = manager.get_or_create(PROFILE).load_target()
            record(
                "new-target-blank",
                "supported",
                "A real target=_blank click created a discoverable Chromium target; actual visibility was observed independently",
                ["AC-09"],
                created=True,
                existingModelSessionStayedOnOriginal=current == main_target,
                worksetAdmission="not implemented; no automatic authorization is inferred",
                acStatus="gap",
                **(await observe_target_visibility(port, session, new_target)),
            )
            await session.close_tab(new_target)
        else:
            record(
                "new-target-blank",
                "failed",
                "The link ref was not available",
                ["AC-09"],
            )
        before = {str(t["id"]) for t in await targets(port)}
        open_ref = find_ref(await structured(session), "window-open")
        if open_ref:
            await session.click(ref=open_ref)
            window_target = await wait_for_target(port, before)
            current = manager.get_or_create(PROFILE).load_target()
            record(
                "new-target-window-open",
                "supported",
                "A real window.open click created a discoverable target; the existing BrowserSession did not auto-follow it; actual visibility was observed independently",
                ["AC-10"],
                created=True,
                modelTargetFollowupRequired=True,
                existingTarget=current,
                acStatus="gap",
                **(await observe_target_visibility(port, session, window_target)),
            )
            await session.close_tab(window_target)
        else:
            record(
                "new-target-window-open",
                "failed",
                "The window.open ref was not available",
                ["AC-10"],
            )

        await session.new_tab(url=f"{origin}/child", force_new=True)
        child_target = manager.get_or_create(PROFILE).load_target()
        assert child_target is not None
        view_client = await connected_client(port, main_target)
        other_client = await connected_client(port, child_target)
        model_dom = await view_client.send("DOM.getDocument", {"depth": -1})
        query = await view_client.send(
            "DOM.querySelector",
            {"nodeId": model_dom["root"]["nodeId"], "selector": "#original"},
        )
        model_refs = RefCache()
        model_refs.store("p0-original", query["nodeId"])
        model_action = await cdp_click(view_client, model_refs, ref="p0-original")
        model_count = await view_client.send(
            "Runtime.evaluate",
            {"expression": "window.originalClicks||0", "returnByValue": True},
        )
        clicked = model_count.get("result", {}).get("value") == 1
        record(
            "dual-target-primitive",
            "supported" if clicked else "unsupported",
            "An independent CDP connection clicked a non-active target exactly once while another target remained active; no Target.activateTarget or session reconnect was used",
            ["AC-11", "AC-12", "AC-42", "AC-43"],
            viewTarget=main_target,
            modelTarget=child_target,
            visibleTarget=manager.get_or_create(PROFILE).load_target(),
            clickSuccess=model_action.success,
            backgroundClickCount=model_count.get("result", {}).get("value"),
            productionAdapter="BrowserTargetSession/registry not implemented",
            acStatus="gap",
        )

        frames: list[dict[str, Any]] = []
        hashes: set[str] = set()

        def on_frame(params: dict[str, Any]) -> None:
            data = str(params.get("data") or "")
            hashes.add(hashlib.sha256(data.encode()).hexdigest())
            frames.append({"at": time.monotonic(), "bytes": len(data)})
            client = params["__client"]
            asyncio.create_task(
                client.send(
                    "Page.screencastFrameAck",
                    {"sessionId": params["sessionId"]},
                    track_activity=False,
                )
            )

        view_client.on("Page.screencastFrame", on_frame)
        await view_client.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 30,
                "maxWidth": 320,
                "maxHeight": 240,
                "everyNthFrame": 1,
            },
        )
        polling_hashes: set[str] = set()
        polling_frames = 0
        started = time.monotonic()
        from octop.api.routers.browser.stream import _capture_jpeg

        view_session = SimpleNamespace(
            _internal=SimpleNamespace(_connected=True, client=view_client)
        )
        while time.monotonic() - started < 60:
            image = await _capture_jpeg(view_session)
            if image:
                polling_frames += 1
                polling_hashes.add(hashlib.sha256(image.encode()).hexdigest())
            await asyncio.sleep(max(0, 1 - (time.monotonic() - started) % 1))
        elapsed = time.monotonic() - started
        await view_client.send("Page.stopScreencast")
        gaps: list[float] = []
        previous = started
        for frame in frames:
            gaps.append(max(0, frame["at"] - previous))
            previous = frame["at"]
        max_gap = max(gaps) if gaps else None
        actual = {
            "elapsedSeconds": elapsed,
            "screencastFrames": len(frames),
            "uniqueScreencastPayloads": len(hashes),
            "existingPollingFrames": polling_frames,
            "uniquePollingPayloads": len(polling_hashes),
            "maxFrameGapSeconds": max_gap,
            "maxFrameGapLimitSeconds": 1.5,
            "viewTargetRemainedBackground": manager.get_or_create(PROFILE).load_target()
            == child_target,
        }
        healthy = (
            elapsed >= 60
            and len(hashes) >= 2
            and len(frames) >= 2
            and max_gap is not None
            and max_gap <= 1.5
            and actual["viewTargetRemainedBackground"]
        )
        record(
            "background-view-60-seconds",
            "supported" if healthy else "unsupported",
            "Captured a non-active viewTarget for a full 60 seconds while another target stayed active; checked real payload changes and the maximum frame gap",
            ["AC-45"],
            **actual,
        )
        view_alive = await view_client.send(
            "Runtime.evaluate", {"expression": "true", "returnByValue": True}
        )
        other_alive = await other_client.send(
            "Runtime.evaluate", {"expression": "true", "returnByValue": True}
        )
        connections_healthy = (
            view_alive.get("result", {}).get("value") is True
            and other_alive.get("result", {}).get("value") is True
        )
        record(
            "dual-target-primitive-execution",
            "supported" if healthy and connections_healthy else "unsupported",
            "Both independent target connections answered real CDP calls after the 60-second capture; no unconditional success is recorded",
            ["AC-11", "AC-42"],
            viewTarget=main_target,
            modelTarget=child_target,
            connectionsHealthy=connections_healthy,
        )

        await harness_and_source()
        daemon = start_daemon_process()
        daemon_pid = int(daemon["pid"])
        started_recording: dict[str, Any] | None = None
        for _ in range(50):
            ping = await send_request({"command": "ping"}, timeout=1)
            if ping.get("ok"):
                break
            await asyncio.sleep(0.1)
        started_recording = await send_request(
            {
                "command": "start",
                "profile": PROFILE,
                "name": "p0-record",
                "privacy": "mask-sensitive",
                "screenshots": "off",
            }
        )
        if not started_recording.get("ok"):
            raise RuntimeError(str(started_recording.get("error") or "Record daemon did not start"))
        await session.switch_tab(main_target)
        record_ref = find_ref(await structured(session), "record-action")
        if not record_ref:
            raise RuntimeError("The isolated recording action is missing")
        await session.click(ref=record_ref)
        await session.wait(text="Recorded action: 1")
        recording_id = str(started_recording["recordingId"])
        stopped = await send_request(
            {
                "command": "stop",
                "recording_id": recording_id,
                "generate_steps": True,
                "name": "p0-record",
            }
        )
        if not stopped.get("ok"):
            raise RuntimeError(str(stopped.get("error") or "Record daemon did not stop"))
        recorded_events = stopped.get("events")
        coordination = await send_request(
            {
                "command": "start",
                "profile": PROFILE,
                "name": "p0-coordination",
                "privacy": "mask-sensitive",
                "screenshots": "off",
            }
        )
        if not coordination.get("ok"):
            raise RuntimeError("The coordination recording did not start")
        main_ws = next(
            str(target["webSocketDebuggerUrl"])
            for target in await targets(port)
            if target["id"] == main_target
        )
        os.environ["OCTOP_P0_TARGET_WS"] = main_ws
        processes = [
            start_writer(f"{origin}/main", "writer-a"),
            start_writer(f"{origin}/main", "writer-b"),
        ]
        exits = [await asyncio.to_thread(process.wait) for process in processes]
        communications = await asyncio.gather(
            *(asyncio.to_thread(process.communicate) for process in processes)
        )
        observations = [
            json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
            for stdout, _ in communications
        ]
        for _ in range(100):
            if len(WRITE_STARTS) >= 2:
                break
            await asyncio.sleep(0.05)
        overlap = len(WRITE_STARTS) >= 2 and WRITE_STARTS[1] - WRITE_STARTS[0] < 0.5
        two_dom_writes = (
            all(code == 0 for code in exits)
            and all(item.get("clickSuccess") is True for item in observations)
            and all(item.get("writeCompleted") is True for item in observations)
            and all(
                item.get("processWrites", 0) > item.get("beforeWrites", 0) for item in observations
            )
            and any(item.get("processWrites", 0) >= 2 for item in observations)
            and len({item.get("documentStartedAt") for item in observations}) == 1
        )
        record(
            "cross-process-write-exclusion",
            "unsupported" if two_dom_writes and overlap else "failed",
            "After a simultaneous readiness barrier, both real OS processes completed DOM writes and distinct POST requests on the same target while the daemon was running; the inspected path has no shared lease or write gate",
            ["AC-15", "AC-40", "AC-51", "AC-60"],
            writerExitCodes=exits,
            writerObservations=observations,
            networkWriteBarrierCompleted=WRITE_BARRIER_COMPLETED,
            networkWritesObserved=len(WRITE_STARTS),
            concurrentArrivalSeconds=WRITE_STARTS[1] - WRITE_STARTS[0]
            if len(WRITE_STARTS) >= 2
            else None,
            daemonWasRunning=True,
            leaseTimeoutCrashBehavior="no shared lease exists to renew, time out or revoke",
        )
        coordination_stopped = await send_request(
            {
                "command": "stop",
                "recording_id": str(coordination["recordingId"]),
                "generate_steps": False,
                "name": "p0-coordination",
            }
        )
        if not coordination_stopped.get("ok"):
            raise RuntimeError("The coordination recording did not stop")
        steps = RecordingStore().read_steps(recording_id)
        generated = SkillGenerator(steps, "p0-record").generate()
        second_generated = SkillGenerator(steps, "p0-record").generate()
        (OUT / "recording-source.json").write_text(
            json.dumps(
                {
                    "recordingId": recording_id,
                    "content": generated,
                    "secondContent": second_generated,
                    "stepCount": len(steps.steps),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        record(
            "octop-recorder-template-baseline",
            "supported" if generated == second_generated else "failed",
            "The real daemon captured an isolated demo action and octop-browser generated its deterministic Bash template without a model; this is not a WebSkill candidate acceptance result",
            ["AC-21"],
            recordingId=recording_id,
            events=recorded_events,
            steps=len(steps.steps),
            screenshots="off",
            templateIdentical=generated == second_generated,
            concurrentFixtureSeparated=True,
        )

        replay_store = RecordingStore()
        replay_id = f"p0-replay-{uuid.uuid4().hex[:8]}"
        replay_store.create(
            RecordingManifest(
                recordingId=replay_id,
                profile=PROFILE,
                createdAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                target={"startUrl": f"{origin}/next"},
            )
        )
        replay_doc = StepsDocument(
            recordingId=replay_id,
            startUrl=f"{origin}/next",
            steps=[{"id": "open-1", "kind": "open", "url": f"{origin}/next"}],
        )
        replay_store.write_steps(replay_id, replay_doc)
        signature = inspect.signature(ReplayRunner)
        replay = await ReplayRunner(replay_store).run(replay_id, profile=PROFILE)
        record(
            "replay-runner-executor-injection",
            "unsupported" if "executor" not in signature.parameters else "supported",
            "Ran the existing ReplayRunner successfully through its default browser_tool path; no executor can currently be injected",
            ["AC-57", "AC-58"],
            constructorParameters=list(signature.parameters),
            defaultReplayStatus=replay.get("status"),
            enhancedPathUsesAuthorizedExecutor=False,
        )
        record(
            "recording-retention-and-delete",
            "unsupported",
            "The real RecordingStore has no delete operation, retention sweeper or per-user capacity limit; 30 days and 50 recordings remain implementation limits, not existing behavior",
            ["AC-56"],
            hasDelete=callable(getattr(replay_store, "delete", None)),
            retentionDays=30,
            maxPerUser=50,
        )

        second_recording = await send_request(
            {
                "command": "start",
                "profile": PROFILE,
                "name": "p0-crash",
                "privacy": "mask-sensitive",
                "screenshots": "off",
            }
        )
        if second_recording.get("ok") and daemon_pid is not None:
            os.kill(daemon_pid, signal.SIGTERM)
            terminated = False
            for _ in range(50):
                if not await is_running():
                    terminated = True
                    break
                await asyncio.sleep(0.1)
            after_crash = await browser_use.ainvoke(
                {"action": "eval_js", "profile": PROFILE, "expression": "true"}
            )
            after_payload = json.loads(after_crash) if isinstance(after_crash, str) else {}
            record(
                "daemon-crash-lease",
                "unsupported" if terminated and after_payload.get("success") is True else "failed",
                "Terminated only the isolated record daemon and confirmed it stopped responding; browser execution remained possible because no cross-process lease or grant revocation exists",
                ["AC-40"],
                daemonTerminated=terminated,
                subsequentWriteSucceeded=after_payload.get("success"),
                leaseRecovered=False,
            )
        else:
            record(
                "daemon-crash-lease",
                "failed",
                "Could not start the isolated crash-test recording",
                ["AC-40"],
            )
    finally:
        if daemon_pid is not None:
            await send_request({"command": "shutdown"}, timeout=1)
        for client in (view_client, other_client):
            if client is not None:
                await client.close()
        await session.close(kill=True)
        first.shutdown()
        second.shutdown()
        first.server_close()
        second.server_close()
        metadata = {
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "profileIsolated": PROFILE,
            "chrome": find_chrome(),
            "python": sys.version,
            "platform": platform.platform(),
            "browserVersion": locals().get("browser_version", {}),
            "results": RESULTS,
        }
        (OUT / "cdp-probes.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Evidence: {OUT / 'cdp-probes.json'}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--writer":
        raise SystemExit(asyncio.run(writer_child(sys.argv[2], sys.argv[3])))
    asyncio.run(run())
    if any(item["status"] == "failed" for item in RESULTS):
        raise SystemExit(1)
