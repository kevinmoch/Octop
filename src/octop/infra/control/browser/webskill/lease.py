"""Translate the shared octop-browser profile lease into WebSkill errors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from octop_browser.cdp.client import CommandGate
from octop_browser.lease import BrowserLeaseError
from octop_browser.lease import lease_path as shared_lease_path
from octop_browser.lease import profile_lease as shared_profile_lease

from .sessions import BrowserAgentError


def lease_path(profile: str) -> Path:
    return shared_lease_path(profile)


@asynccontextmanager
async def profile_lease(profile: str) -> AsyncIterator[None]:
    try:
        async with shared_profile_lease(profile):
            yield
    except BrowserLeaseError as error:
        raise BrowserAgentError(error.code, str(error)) from error


def profile_command_gate(profile: str) -> CommandGate:
    @asynccontextmanager
    async def gate(_method: str, _params: dict[str, Any]) -> AsyncIterator[None]:
        async with profile_lease(profile):
            yield

    return gate
