"""Shared process-local ownership state for browser model and user writes."""

_CONTROL_OWNERS: dict[str, str] = {}


def control_owner_for(profile: str) -> str:
    return _CONTROL_OWNERS.get(profile, "agent")
