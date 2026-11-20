"""Trace-backed page skill candidate retains the portable target contract."""

from __future__ import annotations

from pathlib import Path


def test_trace_generated_skill_uses_fresh_perception_and_semantic_targets() -> None:
    root = Path(__file__).resolve().parents[3]
    text = (root / "scripts/webskill-p1/fixtures/recorded-inspection-trace/SKILL.md").read_text()
    assert "name: recorded-inspection-trace" in text
    assert "version: 1.0.0" in text
    assert "`perceive_page`" in text
    assert "`act_on_page`" in text
    assert "fresh ref" in text
    assert "问题属性" in text and "问题描述" in text
    assert "selector" not in text.lower()
