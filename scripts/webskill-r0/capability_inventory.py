#!/usr/bin/env python3
"""Validate the Octop × WebSkill R0 capability inventory.

The JSON inventory is the machine-readable source of truth. This checker
keeps the reviewed host status honest: a capability marked candidate/supported
must have its expected key in the current host adapter, while a missing port
must not be silently claimed as wired.

Usage:
    python scripts/webskill-r0/capability_inventory.py --check
    python scripts/webskill-r0/capability_inventory.py --summary
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs" / "webskill-server-enhanced" / "capability-inventory.json"
HOST = ROOT / "dashboard" / "webskill" / "src" / "host.ts"

REQUIRED_ENTRY_FIELDS = {
    "id",
    "category",
    "title",
    "sdkSurface",
    "hostWiring",
    "uiSurface",
    "status",
    "decision",
    "phase",
    "requiresSdkChange",
    "currentEvidence",
    "notes",
}

WIRED_STATUSES = {"supported", "candidate", "partial"}
UNWIRED_STATUSES = {"missing", "unsupported", "not-applicable", "blocked"}


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("capability inventory must be a JSON object")
    return value


def validate_matrix(
    inventory: dict[str, Any],
    *,
    host_text: str | None = None,
) -> list[str]:
    errors: list[str] = []
    statuses = set(inventory.get("statusVocabulary") or [])
    decisions = set(inventory.get("decisionVocabulary") or [])
    phases = set(inventory.get("phaseVocabulary") or [])
    if inventory.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if not inventory.get("baseline"):
        errors.append("baseline is required")
    entries = inventory.get("capabilities")
    if not isinstance(entries, list) or not entries:
        return [*errors, "capabilities must be a non-empty list"]

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"capabilities[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        missing = REQUIRED_ENTRY_FIELDS - entry.keys()
        if missing:
            errors.append(f"{where} missing fields: {sorted(missing)}")
            continue
        entry_id = entry["id"]
        if not isinstance(entry_id, str) or not entry_id:
            errors.append(f"{where}.id must be a non-empty string")
            continue
        if entry_id in seen:
            errors.append(f"duplicate capability id: {entry_id}")
        seen.add(entry_id)
        if entry["status"] not in statuses:
            errors.append(f"{entry_id}: unknown status {entry['status']!r}")
        if entry["decision"] not in decisions:
            errors.append(f"{entry_id}: unknown decision {entry['decision']!r}")
        if entry["phase"] not in phases:
            errors.append(f"{entry_id}: unknown phase {entry['phase']!r}")
        if not isinstance(entry["hostWiring"], list):
            errors.append(f"{entry_id}: hostWiring must be a list")
            continue
        if not all(isinstance(item, str) and item for item in entry["hostWiring"]):
            errors.append(f"{entry_id}: hostWiring entries must be non-empty strings")
        if not isinstance(entry["currentEvidence"], list):
            errors.append(f"{entry_id}: currentEvidence must be a list")

        if host_text is None:
            continue
        if entry["status"] in WIRED_STATUSES:
            for token in entry["hostWiring"]:
                if token not in host_text:
                    errors.append(
                        f"{entry_id}: status {entry['status']!r} but host.ts lacks {token!r}"
                    )
        elif entry["status"] in UNWIRED_STATUSES:
            for token in entry["hostWiring"]:
                if token in host_text:
                    errors.append(
                        f"{entry_id}: status {entry['status']!r} but host.ts contains {token!r}"
                    )
    return errors


def summary(inventory: dict[str, Any]) -> str:
    entries = inventory.get("capabilities") or []
    status_counts = Counter(entry.get("status", "?") for entry in entries)
    decision_counts = Counter(entry.get("decision", "?") for entry in entries)
    lines = [f"capabilities: {len(entries)}"]
    lines.append("status: " + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())))
    lines.append("decision: " + ", ".join(f"{k}={v}" for k, v in sorted(decision_counts.items())))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the matrix and host wiring")
    parser.add_argument("--summary", action="store_true", help="print status/decision counts")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--host", type=Path, default=HOST)
    args = parser.parse_args(argv)

    inventory = load_inventory(args.inventory)
    host_text = args.host.read_text(encoding="utf-8") if args.host.is_file() else None
    if args.summary:
        print(summary(inventory))
    if args.check:
        errors = validate_matrix(inventory, host_text=host_text)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print("capability inventory: ok")
    if not args.check and not args.summary:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
