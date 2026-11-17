"""Share profile control ownership with local browser daemon processes."""

from octop_browser.lease import clear_control_owner as clear_shared_control_owner
from octop_browser.lease import read_control_owner as read_shared_control_owner
from octop_browser.lease import write_control_owner as write_shared_control_owner

_CONTROL_OWNERS: dict[str, str] = {}


def control_owner_for(profile: str) -> str:
    if profile not in _CONTROL_OWNERS:
        _CONTROL_OWNERS[profile] = read_shared_control_owner(profile)
    return _CONTROL_OWNERS[profile]


def set_control_owner(profile: str, owner: str) -> None:
    write_shared_control_owner(profile, owner)
    _CONTROL_OWNERS[profile] = owner


def clear_control_owner(profile: str) -> None:
    clear_shared_control_owner(profile)
    _CONTROL_OWNERS.pop(profile, None)
