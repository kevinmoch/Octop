"""Exercise browser-session affinity and Pod replacement through real Ingress."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import subprocess
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "k8s"
KUBECTL = os.environ.get("WEBSKILL_K8S_KUBECTL", str(ROOT / ".artifacts/tools/kubectl"))
NAMESPACE = os.environ.get("WEBSKILL_K8S_NAMESPACE", "webskill-p2")
FIXTURE_URL = "http://webskill-fixture.webskill-p2.svc:8080/"


def safe_text(value: object) -> str:
    text = str(value)
    text = re.sub(r"(?i)(ticket=)[^&\s]+", r"\1[REDACTED]", text)
    return re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)[:2000]


def kubectl(*args: str) -> Any:
    result = subprocess.run(
        [KUBECTL, "-n", NAMESPACE, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def server_pod(upstream: str) -> dict[str, Any]:
    pod_ip = upstream.rsplit(",", 1)[-1].rsplit(":", 1)[0].strip()
    pods = kubectl("get", "pods", "-l", "app.kubernetes.io/component=server", "-o", "json")
    try:
        return next(
            pod
            for pod in pods["items"]
            if pod["status"].get("podIP") == pod_ip and "deletionTimestamp" not in pod["metadata"]
        )
    except StopIteration as error:
        raise AssertionError(
            {
                "upstream": upstream,
                "podIps": [pod["status"].get("podIP") for pod in pods["items"]],
            }
        ) from error


def fixture_cookie_present(user_id: int) -> bool:
    script = """
from pathlib import Path
import sqlite3
import sys
path = Path(f'/data/.octop/browser-profiles/user-{int(sys.argv[1])}/Default/Cookies')
if not path.exists():
    print(False)
else:
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        count = connection.execute(
            "select count(*) from cookies where name='webskill_fixture'"
        ).fetchone()[0]
        print(count > 0)
    finally:
        connection.close()
"""
    result = subprocess.run(
        [
            KUBECTL,
            "-n",
            NAMESPACE,
            "exec",
            "-i",
            "deploy/octop",
            "--",
            "/app/.venv/bin/python",
            "-",
            str(user_id),
        ],
        input=script,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip() == "True"


async def wait_fixture_cookie(user_id: int, expected: bool) -> bool:
    for _ in range(30):
        if fixture_cookie_present(user_id) is expected:
            return expected
        await asyncio.sleep(1)
    return not expected


def require(response: httpx.Response, status: int = 200) -> httpx.Response:
    assert response.status_code == status, (response.status_code, safe_text(response.text))
    return response


async def ws_call(socket: Any, message: dict[str, Any]) -> dict[str, Any]:
    await socket.send(json.dumps(message))
    reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=90))
    assert isinstance(reply, dict)
    return reply


class BrowserAcceptance:
    def __init__(self, base_url: str, tls: ssl.SSLContext, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.tls = tls
        self.headers = {"Origin": self.base_url, "Sec-Fetch-Site": "same-origin"}
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            verify=tls,
            timeout=90,
            headers={"Origin": self.base_url, "Sec-Fetch-Site": "same-origin"},
        )
        self.password = password
        self.token = ""

    async def close(self) -> None:
        await self.client.aclose()

    @property
    def auth(self) -> dict[str, str]:
        assert self.token
        return {"Authorization": f"Bearer {self.token}"}

    def ws_url(self, path: str, ticket: str) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return f"{scheme}://{parts.netloc}{path}?{urlencode({'ticket': ticket})}"

    def ws_connect(self, path: str, ticket: str) -> Any:
        cookies = "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.client.cookies.jar)
        return websockets.connect(
            self.ws_url(path, ticket),
            origin=self.base_url,
            additional_headers={
                "Sec-Fetch-Site": "same-origin",
                **({"Cookie": cookies} if cookies else {}),
            },
            ssl=self.tls if self.base_url.startswith("https:") else None,
            max_size=8 * 1024 * 1024,
        )

    async def login(self, username: str = "admin") -> None:
        response = require(
            await self.client.post(
                "/api/auth/login",
                json={"username": username, "password": self.password},
                headers=self.headers,
            )
        )
        self.token = response.json()["access_token"]
        self.user_id = int(response.json()["user"]["id"])
        assert any(cookie.name == "OCTOP_AFFINITY" for cookie in self.client.cookies.jar)

    async def wait_ready(self) -> None:
        for _ in range(90):
            try:
                response = await self.client.get("/api/health")
                if response.status_code == 200 and response.json().get("ok") is True:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(1)
        raise AssertionError("The replacement Pod did not become ready through Ingress")

    async def create_session(self) -> tuple[str, str]:
        response = require(
            await self.client.post(
                "/api/browser-agent/sessions",
                headers=self.auth,
                json={"startUrl": FIXTURE_URL},
            )
        )
        session_id = response.json()["sessionId"]
        upstream = response.headers.get("x-octop-upstream", "")
        assert upstream, "The acceptance Ingress did not report its upstream Pod"
        require(
            await self.client.get(f"/api/browser-agent/sessions/{session_id}", headers=self.auth)
        )
        return session_id, upstream

    async def ticket(self, session_id: str) -> str:
        response = require(
            await self.client.post(
                f"/api/browser-agent/sessions/{session_id}/tickets",
                headers=self.auth,
            )
        )
        return response.json()["ticket"]

    async def start_browser(self, session_id: str) -> None:
        ticket = await self.ticket(session_id)
        async with self.ws_connect("/api/browser-stream/ws", ticket) as stream:
            await stream.send(
                json.dumps(
                    {
                        "type": "start",
                        "url": FIXTURE_URL,
                        "width": 1280,
                        "height": 800,
                        "reuse_session": True,
                    }
                )
            )
            while True:
                message = json.loads(await asyncio.wait_for(stream.recv(), timeout=90))
                if message.get("type") == "error":
                    raise AssertionError(safe_text(message.get("message")))
                if message.get("type") == "frame":
                    return

    async def perceive(self, socket: Any) -> dict[str, Any]:
        reply = await ws_call(
            socket,
            {"type": "perceive", "requestId": uuid.uuid4().hex, "scope": {"include": ["body"]}},
        )
        assert reply.get("type") == "perceive-result", safe_text(reply)
        return reply

    async def click(self, socket: Any, session_id: str, ref: str) -> None:
        request = {"ref": ref, "action": "click"}
        issued = require(
            await self.client.post(
                f"/api/browser-agent/sessions/{session_id}/grants",
                headers=self.auth,
                json={"request": request},
            )
        )
        reply = await ws_call(
            socket,
            {
                "type": "execute",
                "requestId": uuid.uuid4().hex,
                "request": request,
                "grant": issued.json()["grant"],
            },
        )
        assert reply.get("outcome", {}).get("ok") is True, safe_text(reply)

    async def exercise_browser(self, session_id: str, expected: str, click_sign_in: bool) -> None:
        ticket = await self.ticket(session_id)
        async with self.ws_connect("/api/browser-agent/ws", ticket) as socket:
            perceived = await self.perceive(socket)
            assert expected in json.dumps(perceived, ensure_ascii=False), safe_text(perceived)
            name = "Sign in" if click_sign_in else "Run once"
            target = next(target for target in perceived["targets"] if target.get("name") == name)
            await self.click(socket, session_id, target["ref"])
            if click_sign_in:
                confirmed = await self.perceive(socket)
                assert "signed-in" in json.dumps(confirmed, ensure_ascii=False)


async def run(scenario: str) -> dict[str, Any]:
    base_url = os.environ.get("WEBSKILL_K8S_URL", "https://127.0.0.1:18444")
    ca_file = os.environ.get("WEBSKILL_K8S_CA")
    tls = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    password = os.environ.get("WEBSKILL_K8S_ADMIN_PASSWORD")
    assert password, "WEBSKILL_K8S_ADMIN_PASSWORD is required"
    browser = BrowserAcceptance(base_url, tls, password)
    old_pod: dict[str, Any] | None = None
    try:
        await browser.login()
        username = f"webskill-{uuid.uuid4().hex[:10]}"
        require(
            await browser.client.post(
                "/api/users",
                headers=browser.auth,
                json={
                    "username": username,
                    "password": password,
                    "role": "user",
                    "permissions": ["browser"],
                },
            ),
            201,
        )
        await browser.login(username)
        if scenario == "emptyDir":
            # Pre-provision identities for failover; live user replication is a separate concern.
            kubectl("rollout", "restart", "deployment/octop")
            kubectl("rollout", "status", "deployment/octop", "--timeout=300s")
            await browser.wait_ready()
        session_id, upstream = await browser.create_session()
        old_pod = server_pod(upstream)
        await browser.start_browser(session_id)
        await browser.exercise_browser(session_id, "anonymous", click_sign_in=True)
        repeated = require(
            await browser.client.get(
                f"/api/browser-agent/sessions/{session_id}", headers=browser.auth
            )
        )
        assert repeated.headers.get("x-octop-upstream", "") == upstream, (
            upstream,
            repeated.headers.get("x-octop-upstream", ""),
        )
        cookie_before = await wait_fixture_cookie(browser.user_id, True)
        assert cookie_before, "The persistent fixture cookie was not written before Pod deletion"
        before = {
            "sessionReadAfterRealClick": True,
            "upstreamPod": old_pod["metadata"]["name"],
            "upstreamUid": old_pod["metadata"]["uid"],
            "affinityCookie": True,
            "cookiePresentBeforeReplacement": cookie_before,
        }
        await browser.client.aclose()
        kubectl("delete", "pod", old_pod["metadata"]["name"], "--wait=true")
        kubectl("rollout", "status", "deployment/octop", "--timeout=300s")
        browser.client = httpx.AsyncClient(
            base_url=browser.base_url,
            verify=tls,
            timeout=90,
            headers=browser.headers,
        )
        await browser.wait_ready()
        missing = require(
            await browser.client.get(
                f"/api/browser-agent/sessions/{session_id}", headers=browser.auth
            ),
            404,
        )
        replacement_session, replacement_upstream = await browser.create_session()
        new_pod = server_pod(replacement_upstream)
        assert new_pod["metadata"]["uid"] != old_pod["metadata"]["uid"], (
            old_pod["metadata"]["uid"],
            new_pod["metadata"]["uid"],
        )
        cookie_after = await wait_fixture_cookie(browser.user_id, scenario == "persistent")
        assert cookie_after is (scenario == "persistent"), (
            "The persisted cookie does not match the storage scenario",
            cookie_after,
        )
        await browser.start_browser(replacement_session)
        if scenario == "persistent":
            await browser.exercise_browser(replacement_session, "signed-in", click_sign_in=False)
        else:
            await browser.exercise_browser(replacement_session, "anonymous", click_sign_in=True)
        replicas = kubectl("get", "deployment", "octop", "-o", "json")["spec"]["replicas"]
        return {
            "status": "passed",
            "scenario": scenario,
            "replicas": replicas,
            "beforeReplacement": before,
            "oldSessionStatusAfterReplacement": missing.status_code,
            "replacementPod": new_pod["metadata"]["name"],
            "replacementUid": new_pod["metadata"]["uid"],
            "loginStateAfterReplacement": "signed-in" if scenario == "persistent" else "anonymous",
            "newSessionReadAndAction": True,
            "crossPodLockClaimed": False,
        }
    except Exception as error:
        return {
            "status": "failed",
            "scenario": scenario,
            "errorType": type(error).__name__,
            "error": safe_text(error),
            "podBeforeFailure": old_pod["metadata"]["name"] if old_pod is not None else None,
        }
    finally:
        await browser.client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("persistent", "emptyDir"), required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.scenario))
    OUT.mkdir(parents=True, exist_ok=True)
    result_path = OUT / f"{args.scenario}-result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
