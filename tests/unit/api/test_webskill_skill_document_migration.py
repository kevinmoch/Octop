"""The installed prompt-only skill keeps the portable page-action contract."""

from __future__ import annotations

from pathlib import Path


def test_recorded_inspection_skill_uses_semantic_page_tools() -> None:
    root = Path(__file__).resolve().parents[3]
    text = (root / "scripts/webskill-p1/fixtures/recorded-inspection-skill/SKILL.md").read_text()
    assert "name: recorded-inspection-page" in text
    assert "`perceive_page`" in text
    assert "`act_on_page`" in text
    assert "问题属性" in text
    assert "问题描述" in text
    assert "selector" not in text.lower()
    assert "ref" not in text.lower().split("fresh ref", 1)[0]
