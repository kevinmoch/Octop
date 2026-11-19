"""Share profile control ownership with local browser daemon processes."""

from octop_browser.lease import clear_control_owner as clear_shared_control_owner
from octop_browser.lease import read_control_owner as read_shared_control_owner
from octop_browser.lease import write_control_owner as write_shared_control_owner


def control_owner_for(profile: str) -> str:
    return read_shared_control_owner(profile)


def set_control_owner(profile: str, owner: str) -> None:
    write_shared_control_owner(profile, owner)


def clear_control_owner(profile: str) -> None:
    clear_shared_control_owner(profile)
