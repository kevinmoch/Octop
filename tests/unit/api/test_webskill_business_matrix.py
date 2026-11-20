"""Shape checks for the repeatable CCS business matrix."""

from __future__ import annotations

import json
from pathlib import Path


def test_business_matrix_config_is_unique_and_has_home_page() -> None:
    root = Path(__file__).resolve().parents[3]
    config = json.loads((root / "scripts/webskill-p1/business_matrix.json").read_text())
    tabs = [item["tab"] for item in config]
    names = [item["name"] for item in config]
    assert "现场巡检问题" in tabs
    assert len(tabs) == len(set(tabs))
    assert len(names) == len(set(names))
    assert all(isinstance(item.get("expectFields"), list) for item in config)
