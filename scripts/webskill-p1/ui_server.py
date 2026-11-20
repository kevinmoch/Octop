"""Run an isolated, non-production P1 UI acceptance server."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ["BROWSER_USE_PROFILES_DIR"] = str(OUT / "profiles")
os.environ["BROWSER_USE_CDP_PORT_START"] = "43000"
os.environ["BROWSER_USE_SCREENSHOTS_DIR"] = str(OUT / "screenshots")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from tests.support.app import ensure_control_plane_bound  # noqa: E402
from tests.support.auth import TEST_PASSWORD, bootstrap_admin  # noqa: E402

from octop.api.app import build_app  # noqa: E402
from octop.infra.server import OctopServer  # noqa: E402


async def serve() -> None:
    server = OctopServer(home=OUT / "home")
    with contextlib.redirect_stdout(io.StringIO()):
        await server.start()
    await ensure_control_plane_bound(server)
    app = build_app(server)
    if os.environ.get("WEBSKILL_P1_LNA_POLICY") == "1":
        inner_app = app

        async def app(scope, receive, send):
            if scope["type"] == "http" and not scope.get("path", "").startswith("/api/"):

                async def send_with_policy(message):
                    if message["type"] == "http.response.start":
                        message["headers"] = [
                            *message.get("headers", []),
                            (
                                b"permissions-policy",
                                b"local-network-access=(), local-network=(), loopback-network=()",
                            ),
                        ]
                    await send(message)

                await inner_app(scope, receive, send_with_policy)
            else:
                await inner_app(scope, receive, send)

    if server.user_manager is not None and server.user_manager.get_by_id(1) is None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            await bootstrap_admin(client, OUT / "home")
    host = os.environ.get("WEBSKILL_P1_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBSKILL_P1_UI_PORT", "18088"))
    scheme = "https" if os.environ.get("WEBSKILL_P1_UI_CERTFILE") else "http"
    print(f"WebSkill P1 isolated UI: {scheme}://{host}:{port}/browser", flush=True)
    print("Test-only credentials are the standard isolated fixture account.", flush=True)
    uv = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            ws_ping_interval=20.0,
            ws_ping_timeout=None,
            ssl_certfile=os.environ.get("WEBSKILL_P1_UI_CERTFILE"),
            ssl_keyfile=os.environ.get("WEBSKILL_P1_UI_KEYFILE"),
        )
    )
    try:
        await uv.serve()
    finally:
        await server.stop()


if __name__ == "__main__":
    if os.environ.get("WEBSKILL_P1_SHOW_TEST_PASSWORD") == "1":
        print(TEST_PASSWORD)
    asyncio.run(serve())
