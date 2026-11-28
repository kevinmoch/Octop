"""Outbound proxy mode: config parsing and process-wide application."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import octop.infra.utils.outbound_proxy as outbound_proxy
from octop.config import load_config, validate_persisted_config
from octop.infra.utils.outbound_proxy import (
    apply_outbound_proxy_mode,
    outbound_direct_active,
)


@pytest.fixture(autouse=True)
def _restore_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep each test's env edits and module snapshot state inside the test."""
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "no_proxy",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(outbound_proxy, "_saved", {})
    monkeypatch.setattr(outbound_proxy, "_direct_applied", False)


def test_config_defaults_to_system(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "config.json")
    assert cfg.outbound_proxy_mode == "system"
    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert written["outbound_proxy_mode"] == "system"


def test_config_loads_direct_from_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"outbound_proxy_mode": "direct"}), encoding="utf-8")
    assert load_config(path).outbound_proxy_mode == "direct"


def test_config_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_OUTBOUND_PROXY_MODE", "direct")
    assert load_config(tmp_path / "config.json").outbound_proxy_mode == "direct"


def test_config_env_override_invalid_falls_back_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOP_OUTBOUND_PROXY_MODE", "bogus")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"outbound_proxy_mode": "system"}), encoding="utf-8")
    assert load_config(path).outbound_proxy_mode == "system"


def test_config_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="outbound_proxy_mode"):
        validate_persisted_config({"outbound_proxy_mode": "socks5"})


def test_apply_direct_forces_httpx_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:8080")
    apply_outbound_proxy_mode("direct")
    try:
        assert outbound_direct_active()
        with httpx.Client() as client:
            transport = client._transport_for_url(httpx.URL("http://example.com/"))
            assert transport is client._transport
    finally:
        apply_outbound_proxy_mode("system")


def test_apply_direct_is_idempotent_and_restores_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:8080")
    apply_outbound_proxy_mode("direct")
    apply_outbound_proxy_mode("direct")
    assert outbound_direct_active()
    apply_outbound_proxy_mode("system")
    assert not outbound_direct_active()
    import os

    assert os.environ.get("HTTPS_PROXY") == "http://10.0.0.1:8080"
    assert "NO_PROXY" not in os.environ


def test_apply_system_without_direct_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:8080")
    apply_outbound_proxy_mode("system")
    import os

    assert os.environ.get("HTTP_PROXY") == "http://10.0.0.1:8080"
    assert not outbound_direct_active()
