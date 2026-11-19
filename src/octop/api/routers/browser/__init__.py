"""Browser routers — env/install, octop-browser, record/replay, WS screencast."""

from __future__ import annotations

from fastapi import APIRouter

from octop.api.routers.browser.env import router as env_router
from octop.api.routers.browser.harness import router as harness_router
from octop.api.routers.browser.record_replay import router as record_replay_router
from octop.api.routers.browser.replay import router as replay_router
from octop.api.routers.browser.stream import router as stream_router
from octop.api.routers.browser.uninstall import router as uninstall_router
from octop.api.routers.browser.webskill import router as webskill_router

router = APIRouter()
router.include_router(env_router)
router.include_router(harness_router)
router.include_router(record_replay_router)
router.include_router(replay_router)
router.include_router(stream_router)
router.include_router(uninstall_router)
router.include_router(webskill_router)

__all__ = ["router"]
