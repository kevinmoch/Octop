"""Portable semantic-step contract for a recorded WebSkill page workflow."""

from __future__ import annotations

import json
from pathlib import Path


def test_recorded_page_skill_uses_only_semantic_targets() -> None:
    root = Path(__file__).resolve().parents[3]
    skill = json.loads((root / "scripts/webskill-p1/fixtures/recorded-page-skill.json").read_text())
    assert skill["steps"]
    for step in skill["steps"]:
        assert step["action"] in {
            "click",
            "fill",
            "select",
            "set",
            "hover",
            "drag",
            "submit",
            "scroll",
            "back",
        }
        target = step["target"]
        assert set(target) <= {"role", "name", "frame"}
        assert target["role"]
        assert "ref" not in target
        assert "selector" not in target
        assert "x" not in target and "y" not in target
