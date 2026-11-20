"""Write-and-restore smoke for the live PageAgent contract.

This intentionally changes only two unsaved form fields, verifies the change, then
restores the original values. It never clicks Save/Submit/Approve.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from octop_browser.lease import read_control_owner
from playwright.async_api import async_playwright

PROFILE_DIR = Path.home() / ".octop" / "browser-profiles"


async def run(cdp_url: str, page_substring: str, *, write: bool) -> dict[str, Any]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = next(
                (
                    candidate
                    for context in browser.contexts
                    for candidate in context.pages
                    if page_substring in candidate.url
                ),
                None,
            )
            if page is None:
                raise RuntimeError(f"No page containing {page_substring!r} is open")
            return await page.evaluate(
                """async ({write}) => {
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
                        const id = 'write-smoke-' + (++sequence);
                        pending.set(id, resolve);
                        ws.send(JSON.stringify({...payload, requestId: id}));
                        setTimeout(() => reject(new Error('timeout: ' + payload.type)), 20000);
                    });
                    const flatten = (nodes) => {
                        const output = [];
                        const visit = (items) => { for (const item of items || []) { output.push(item); if (Array.isArray(item.children)) visit(item.children); } };
                        visit(nodes);
                        return output;
                    };
                    const perceive = async () => {
                        const reply = await request({type: 'perceive', scope: {include: ['body']}, actionScope: {include: ['body']}});
                        return flatten(reply.result?.nodes || []);
                    };
                    const act = async (target, action, value) => {
                        if (!target) return {skipped: true};
                        const actionRequest = {ref: target.ref, action, ...(value !== undefined ? {value} : {})};
                        const grantReply = await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid) + '/grants', {
                            method: 'POST', headers, body: JSON.stringify({request: actionRequest}),
                        });
                        if (!grantReply.ok) return {ok: false, grantStatus: grantReply.status};
                        const grant = await grantReply.json();
                        return (await request({type: 'execute', request: actionRequest, grant: grant.grant})).outcome;
                    };
                    const fields = (nodes) => ({
                        attr: nodes.find((node) => node.name === '问题属性'),
                        desc: nodes.find((node) => node.name === '问题描述'),
                    });
                    const initial = fields(await perceive());
                    if (!initial.attr || !initial.desc) {
                        ws.close();
                        await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid), {method: 'DELETE', headers});
                        return {skipped: true, reason: 'required fields are not present'};
                    }
                    const originalAttr = initial.attr.value || '';
                    const originalDesc = initial.desc.value || '';
                    const alternate = originalAttr === '规格不符' ? '产地不符' : '规格不符';
                    const result = {original: {attr: originalAttr, desc: originalDesc}, alternate, write, steps: {}};
                    try {
                        if (write) {
                            result.steps.selectAlternate = await act(initial.attr, 'select', alternate);
                            result.steps.fillTemp = await act(initial.desc, 'fill', originalDesc + '暂存');
                            const changed = fields(await perceive());
                            result.changed = {attr: changed.attr?.value, desc: changed.desc?.value};
                        }
                    } finally {
                        // Restore even if a write step failed. No save/approval is ever clicked.
                        const restoreFields = fields(await perceive());
                        result.steps.selectRestore = await act(restoreFields.attr, 'select', originalAttr);
                        result.steps.fillRestore = await act(restoreFields.desc, 'fill', originalDesc);
                        const restored = fields(await perceive());
                        result.restored = {attr: restored.attr?.value, desc: restored.desc?.value};
                    }
                    ws.close();
                    await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid), {method: 'DELETE', headers});
                    return result;
                }""",
                {"write": write},
            )
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9223")
    parser.add_argument("--page", default="/browser")
    parser.add_argument("--write", action="store_true", help="perform and restore a real field edit")
    args = parser.parse_args()
    result = asyncio.run(run(args.cdp, args.page, write=args.write))
    result["controlOwner"] = read_control_owner("user-1", profiles_dir=PROFILE_DIR)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("skipped"):
        return 0
    restored = result.get("restored", {})
    original = result.get("original", {})
    return 0 if restored == original else 1


if __name__ == "__main__":
    raise SystemExit(main())
