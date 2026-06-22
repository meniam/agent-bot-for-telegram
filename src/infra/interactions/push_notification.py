"""PushNotification SDK tool flow.

Forwards the SDK-supplied message to Telegram as a plain `🔔 ...` bubble,
then tells Claude the notification was delivered so it does not retry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import PermissionResultDeny

if TYPE_CHECKING:
    from .gate import TelegramInteractionGate

log = logging.getLogger(__name__)


async def handle(
    gate: TelegramInteractionGate,
    chat_id: int,
    tool_input: dict[str, Any],
) -> PermissionResultDeny:
    """Deliver the notification to Telegram and report the outcome to the model.

    Always returns a *Deny-shaped* result on purpose: the SDK feeds
    ``PermissionResultDeny.message`` back as the tool result, so the message
    carries the delivery status (delivered / empty / failed), not a refusal.
    """
    message = str(tool_input.get("message", "") or "").strip()
    if not message:
        return PermissionResultDeny(
            message="Empty notification — nothing to deliver.",
        )
    body = f"🔔 {message}"
    # Telegram allows 4096 chars; the upstream contract caps the model at
    # 200 but defend just in case.
    if len(body) > 4000:
        body = body[:3997] + "..."
    try:
        await gate._bot.send_message(chat_id, body, parse_mode=None)
    except Exception as e:
        log.exception("PushNotification: failed to send to Telegram")
        return PermissionResultDeny(
            message=f"Failed to deliver notification: {e!r}",
        )
    return PermissionResultDeny(
        message="Notification delivered to user via Telegram.",
    )
