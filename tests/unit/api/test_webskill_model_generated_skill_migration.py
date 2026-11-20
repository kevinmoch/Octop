"""A real trace-generated SKILL.md stays semantic and re-installable."""

from __future__ import annotations

from pathlib import Path


def test_model_generated_skill_has_no_runtime_ref_or_selector() -> None:
    root = Path(__file__).resolve().parents[3]
    text = (
        root / "scripts/webskill-p1/fixtures/generated-fill-inspection-issue-form/SKILL.md"
    ).read_text()
    assert "name: fill-inspection-issue-form" in text
    assert "`perceive_page`" in text
    assert "`act_on_page`" in text
    assert "问题属性" in text and "问题描述" in text
    assert "fresh `perceive_page`" in text
    assert "ws_" not in text
    assert "selector" not in text.lower()
    assert "http://" not in text
