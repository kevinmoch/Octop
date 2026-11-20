"""Replay a semantic page skill through the Octop PageAgent.

The input intentionally contains only role/name/frame/action/value. It never
contains a ref, selector, coordinate or CDP id, so it exercises the same
portable target contract used by a WebSkill trace-generated skill.
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


def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for node in nodes:
        output.append(node)
        output.extend(flatten(node.get("children", [])))
    return output


async def run(cdp_url: str, page_substring: str, skill_path: Path) -> dict[str, Any]:
    skill = json.loads(skill_path.read_text())
    steps = skill.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("The skill has no steps")
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
            result = await page.evaluate(
                """async (steps) => {
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
                        const id = 'skill-' + (++sequence);
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
                    const results = [];
                    try {
                        for (const step of steps) {
                            const target = step.target || {};
                            const perceived = await request({type: 'perceive', scope: {include: ['body']}, actionScope: {include: ['body']}});
                            const nodes = flatten(perceived.result?.nodes || []);
                            const match = nodes.find((node) => node.role === target.role && node.name === target.name && node.ref);
                            if (!match) {
                                results.push({action: step.action, ok: false, reason: 'semantic target not found', target});
                                break;
                            }
                            const actionRequest = {ref: match.ref, action: step.action, ...(step.value !== undefined ? {value: step.value} : {})};
                            const grantReply = await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid) + '/grants', {
                                method: 'POST', headers, body: JSON.stringify({request: actionRequest}),
                            });
                            if (!grantReply.ok) {
                                results.push({action: step.action, ok: false, target, reason: 'grant ' + grantReply.status});
                                break;
                            }
                            const grant = await grantReply.json();
                            const reply = await request({type: 'execute', request: actionRequest, grant: grant.grant});
                            results.push({action: step.action, target, outcome: reply.outcome || reply});
                            if (!reply.outcome || reply.outcome.ok !== true) break;
                        }
                    } finally {
                        ws.close();
                        await fetch('/api/browser-agent/sessions/' + encodeURIComponent(sid), {method: 'DELETE', headers});
                    }
                    return {skill: steps, results};
                }""",
                steps,
            )
            result["controlOwner"] = read_control_owner("user-1", profiles_dir=PROFILE_DIR)
            return result
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9223")
    parser.add_argument("--page", default="/browser")
    parser.add_argument(
        "--skill",
        type=Path,
        default=Path(__file__).with_name("fixtures") / "recorded-page-skill.json",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args.cdp, args.page, args.skill))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(item.get("outcome", {}).get("ok") is True for item in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
