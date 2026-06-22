"""Fail-closed access-control predicate.

No code path may let a message reach the agent without this check. Order:
``blacklist_chat_ids`` denies first; then ``allowed_for_all`` admits any
non-blacklisted chat; otherwise the sender must be in ``allowed_chat_ids``. A
missing/empty allow-list with ``allowed_for_all=false`` denies everyone.
"""

import logging
from collections.abc import Callable

from ..config import BotConfig


def make_acl(cfg: BotConfig, glog: logging.Logger) -> Callable[[int], bool]:
    """Build the fail-closed ``is_allowed`` predicate; log the resulting policy."""
    allowed_set: set[int] = set(cfg.allowed_chat_ids)
    blacklist_set: set[int] = set(cfg.blacklist_chat_ids)

    def is_allowed(chat_id: int) -> bool:
        """Whether ``chat_id`` may pass the gate (blacklist denies first)."""
        if chat_id in blacklist_set:
            return False
        if cfg.allowed_for_all:
            return True
        return chat_id in allowed_set

    if cfg.allowed_for_all:
        glog.warning(
            "[%s] access: OPEN TO EVERYONE (allowed_for_all=true)", cfg.name
        )
    else:
        glog.info(
            "[%s] access restricted to %d chat_id(s)", cfg.name, len(allowed_set)
        )
    if blacklist_set:
        glog.info("[%s] blacklist: %d chat_id(s)", cfg.name, len(blacklist_set))

    return is_allowed
