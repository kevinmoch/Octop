"""Read-only business tab matrix plus optional write/restore smoke.

Each tab is opened through the dashboard, perceived by a fresh PageAgent session,
and checked against the configured expected field names. The script always
returns to 现场巡检问题 and restores agent ownership.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from octop_browser.lease import read_control_owner, write_control_owner
from playwright.async_api import async_playwright

PROFILE_DIR = Path.home() / ".octop" / "browser-profiles"
HOME_TAB = "现场巡检问题"


async def perceive(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """async () => {
            const token = localStorage.getItem('auth_token') || sessionStorage.getItem('auth_token');
            if (!token) throw new Error('The dashboard session is not authenticated');
            const headers = {'content-type': 'application/json', 'authorization': 'Bearer ' + token};
            const created = await fetch('/api/browser-agent/sessions', {
                method: 'POST', headers, body: JSON.stringify({startUrl: ''}),
            });
            if (!created.ok) throw new Error('Could not create a browser-agent session');
            const sid = (await created.json()).sessionId;
            const ticket = await (await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid) + '/tickets', {method: 'POST', headers, body: '{}'})).json();
            const ws = new WebSocket('ws://' + location.host + '/api/browser-agent/ws?ticket=' + encodeURIComponent(ticket.ticket));
            await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
            let sequence = 0;
            const pending = new Map();
            ws.onmessage = (event) => {
                const message = JSON.parse(event.data);
                if (message.requestId && pending.has(message.requestId)) {
                    pending.get(message.requestId)(message);
                    pending.delete(message.requestId);
                }
            };
            const request = (payload) => new Promise((resolve, reject) => {
                const id = 'matrix-' + (++sequence);
                pending.set(id, resolve);
                ws.send(JSON.stringify({...payload, requestId: id}));
                setTimeout(() => reject(new Error('timeout: ' + payload.type)), 20000);
            });
            const started = performance.now();
            const reply = await request({type: 'perceive', scope: {include: ['body']}, actionScope: {include: ['body']}});
            const elapsed = Math.round(performance.now() - started);
            const flatten = (nodes) => {
                const output = [];
                const visit = (items) => {
                    for (const item of items || []) {
                        output.push(item);
                        if (Array.isArray(item.children)) visit(item.children);
                    }
                };
                visit(nodes);
                return output;
            };
            const nodes = flatten(reply.result?.nodes || []);
            const out = {
                title: reply.result?.page?.title,
                nodes: nodes.length,
                targets: (reply.targets || []).length,
                names: [...new Set(nodes.map((node) => node.name).filter((name) => typeof name === 'string'))],
                perceiveMs: elapsed,
                error: reply.error || null,
            };
            ws.close();
            await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid), {method: 'DELETE', headers});
            return out;
        }"""
    )


async def run(cdp_url: str, config_path: Path, *, write: bool, budget_ms: int) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    failures: list[str] = []
    pages: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            dashboard = next(
                (
                    candidate
                    for context in browser.contexts
                    for candidate in context.pages
                    if "/browser" in candidate.url
                ),
                None,
            )
            if dashboard is None:
                raise RuntimeError("The Octop browser dashboard is not open")
            await dashboard.reload(wait_until="domcontentloaded")
            await dashboard.wait_for_timeout(5000)
            try:
                for item in config:
                    name = item["name"]
                    try:
                        await dashboard.get_by_role(
                            "tab", name=item["tab"], exact=True
                        ).click(timeout=5000)
                    except Exception as error:
                        failures.append(f"{name}: could not activate tab: {error}")
                        pages.append({"name": name, "error": "tab-activation-failed"})
                        continue
                    await dashboard.wait_for_timeout(2500)
                    try:
                        result = await perceive(dashboard)
                    except Exception as error:
                        failures.append(f"{name}: perception failed: {error}")
                        pages.append({"name": name, "error": "perception-failed"})
                        continue
                    result["name"] = name
                    result["expectedFields"] = item.get("expectFields", [])
                    if result.get("perceiveMs", 0) > budget_ms:
                        failures.append(f"{name}: perceive {result['perceiveMs']}ms over {budget_ms}ms")
                    missing = [
                        field for field in result["expectedFields"] if field not in result["names"]
                    ]
                    if missing:
                        failures.append(f"{name}: missing fields {missing}")
                    pages.append(result)
            finally:
                try:
                    await dashboard.get_by_role(
                        "tab", name=HOME_TAB, exact=True
                    ).click(timeout=5000)
                    await dashboard.wait_for_timeout(2500)
                except Exception as error:
                    failures.append(f"could not restore {HOME_TAB}: {error}")
                write_control_owner("user-1", "agent", profiles_dir=PROFILE_DIR)
            write_result: dict[str, Any] | None = None
            if write:
                script = Path(__file__).with_name("page_semantics_write_smoke.py")
                completed = subprocess.run(
                    [sys.executable, str(script), "--write"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                write_result = {
                    "exitCode": completed.returncode,
                    "stdout": completed.stdout[-8000:],
                    "stderr": completed.stderr[-2000:],
                }
                if completed.returncode != 0:
                    failures.append(f"write/restore smoke exited {completed.returncode}")
            return {
                "pages": pages,
                "failures": failures,
                "write": write_result,
                "controlOwner": read_control_owner("user-1", profiles_dir=PROFILE_DIR),
            }
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9223")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("business_matrix.json"),
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--budget-ms", type=int, default=3000)
    args = parser.parse_args()
    result = asyncio.run(run(args.cdp, args.config, write=args.write, budget_ms=args.budget_ms))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
