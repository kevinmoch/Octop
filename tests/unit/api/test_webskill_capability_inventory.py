"""R0 capability inventory contract and host-wiring audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "webskill-r0" / "capability_inventory.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("webskill_capability_inventory", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_is_valid_and_covers_declared_ports() -> None:
    module = load_module()
    inventory = module.load_inventory()
    host = (ROOT / "dashboard/webskill/src/host.ts").read_text(encoding="utf-8")
    assert module.validate_matrix(inventory, host_text=host) == []

    by_id = {entry["id"]: entry for entry in inventory["capabilities"]}
    required = {
        "native-skill-generation",
        "page-tabs",
        "page-paging",
        "pdf-input",
        "page-image",
        "downloads",
        "remote-image",
        "web-office",
        "web-office-template",
        "dwg",
        "page-declared-skill",
    }
    assert required <= by_id.keys()
    assert by_id["native-skill-generation"]["status"] == "candidate"
    assert by_id["native-skill-generation"]["decision"] == "keep"
    assert by_id["page-tabs"]["status"] == "candidate"
    assert by_id["page-paging"]["status"] == "candidate"
    assert by_id["page-image"]["status"] == "candidate"
    assert by_id["pdf-input"]["status"] == "candidate"
    assert by_id["downloads"]["status"] == "candidate"
    assert by_id["remote-image"]["status"] == "unsupported"
    assert by_id["web-office"]["status"] == "unsupported"
    assert by_id["dwg"]["status"] == "unsupported"


def test_two_host_scenario_contract_covers_native_and_resource_ports() -> None:
    import json

    path = ROOT / "docs/webskill-server-enhanced/r0-two-host-scenarios.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == 1
    scenarios = document["scenarios"]
    by_id = {scenario["id"]: scenario for scenario in scenarios}
    assert len(by_id) == len(scenarios)
    required = {
        "native-skill-generate-reuse",
        "page-tabs",
        "page-paging",
        "pdf-read",
        "page-image",
        "downloads",
        "remote-image-unsupported",
        "web-office-unsupported",
        "dwg-unsupported",
        "trusted-tls",
        "docker-wheel",
    }
    assert required <= by_id.keys()
    assert by_id["native-skill-generate-reuse"]["modelKind"] == "external"
    assert by_id["trusted-tls"]["status"] == "blocked"
    assert by_id["docker-wheel"]["status"] == "blocked"
    for scenario in scenarios:
        assert scenario["hosts"]
        assert scenario["phase"] in {"R0", "R1", "R2A", "R2B", "R2C", "R3", "R4"}
        assert scenario["expected"]
