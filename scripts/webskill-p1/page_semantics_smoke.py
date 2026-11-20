"""Run a small, non-destructive PageAgent smoke check against the live browser UI.

Assumes the Octop dashboard is already logged in and its WebSkill browser panel is
open. It never writes a new field value: select/fill use the value already shown.
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


async def run(cdp_url: str, page_substring: str) -> dict[str, Any]:
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
            await page.wait_for_timeout(1000)
            result = await page.evaluate(
                """async () => {
                    const token = localStorage.getItem('auth_token') || sessionStorage.getItem('auth_token');
                    if (!token) throw new Error('The dashboard session is not authenticated');
                    const headers = {'content-type': 'application/json', 'authorization': 'Bearer ' + token};
                    const created = await fetch('/api/browser-agent/sessions', {
                        method: 'POST', headers, body: JSON.stringify({startUrl: ''}),
                    });
                    if (!created.ok) throw new Error('Could not create a browser-agent session: ' + created.status);
                    const session = await created.json();
                    const sid = session.sessionId;
                    const ticketReply = await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid) + '/tickets', {method: 'POST', headers, body: '{}'});
                    const ticket = await ticketReply.json();
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
                        const id = 'smoke-' + (++sequence);
                        pending.set(id, resolve);
                        ws.send(JSON.stringify({...payload, requestId: id}));
                        setTimeout(() => reject(new Error('timeout: ' + payload.type)), 20000);
                    });
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
                    const summarize = async (target, action, value) => {
                        if (!target) return {skipped: true};
                        const startedAt = performance.now();
                        const actionRequest = {ref: target.ref, action, ...(value !== undefined ? {value} : {})};
                        const grantReply = await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid) + '/grants', {
                            method: 'POST', headers, body: JSON.stringify({request: actionRequest}),
                        });
                        if (!grantReply.ok) return {ok: false, grantStatus: grantReply.status};
                        const grant = await grantReply.json();
                        const reply = await request({type: 'execute', request: actionRequest, grant: grant.grant});
                        return {...(reply.outcome || reply), durationMs: Math.round(performance.now() - startedAt)};
                    };
                    const perceiveStarted = performance.now();
                    const perceived = await request({type: 'perceive', scope: {include: ['body']}, actionScope: {include: ['body']}});
                    const perceiveMs = Math.round(performance.now() - perceiveStarted);
                    const nodes = flatten(perceived.result?.nodes || []);
                    const find = (name) => nodes.find((node) => node.name === name);
                    const attr = find('问题属性');
                    const desc = find('问题描述');
                    const select = attr ? await summarize(attr, 'select', attr.value || '规格不符') : {skipped: true};
                    const fill = desc ? await summarize(desc, 'fill', desc.value || '测试') : {skipped: true};
                    const hover = attr ? await summarize(attr, 'hover') : {skipped: true};
                    ws.close();
                    await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid), {method: 'DELETE', headers});
                    return {
                        page: perceived.result?.page || {},
                        nodes: nodes.length,
                        targets: (perceived.targets || []).length,
                        perceiveMs,
                        fields: {
                            attr: attr ? {role: attr.role, name: attr.name, value: attr.value} : null,
                            description: desc ? {role: desc.role, name: desc.name, value: desc.value} : null,
                        },
                        actions: {select, fill, hover},
                    };
                }"""
            )
            result["controlOwner"] = read_control_owner("user-1", profiles_dir=PROFILE_DIR)
            return result
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9223")
    parser.add_argument("--page", default="/browser")
    args = parser.parse_args()
    result = asyncio.run(run(args.cdp, args.page))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    actions = result.get("actions", {})
    ok = all(
        outcome.get("ok") is True or outcome.get("skipped") is True
        for outcome in actions.values()
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
