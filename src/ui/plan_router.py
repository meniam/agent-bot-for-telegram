"""Per-chat `/plan` arming state and helper for engaging plan mode.

`/plan` with no arguments arms the chat — the next text or transcribed voice
message becomes the plan prompt. `fire` engages plan mode in the agent and
runs one turn with the supplied prompt.
"""

import logging
from collections.abc import Awaitable, Callable

from aiogram.types import Message

from ..i18n import Translator
from ..infra.agent import AgentBackend
from ..infra.interactions import TelegramInteractionGate
from .markdown import send_md


class PlanRouter:
    """Track which chats have armed `/plan` and engage plan mode on fire."""

    def __init__(
        self,
        agent: AgentBackend,
        gate: TelegramInteractionGate,
        tr: Translator,
        glog: logging.Logger,
        bot_name: str,
    ) -> None:
        """Store collaborators and start with no chats armed."""
        self._agent = agent
        self._gate = gate
        self._tr = tr
        self._glog = glog
        self._bot_name = bot_name
        self._armed: set[int] = set()

    def is_armed(self, chat_id: int) -> bool:
        """Return whether `/plan` is armed for the chat."""
        return chat_id in self._armed

    def arm(self, chat_id: int, cl: logging.Logger) -> None:
        """Arm the chat so the next text/voice message becomes the plan prompt."""
        self._armed.add(chat_id)
        cl.info("/plan armed — waiting for next text/voice message")

    def disarm(self, chat_id: int) -> None:
        """Clear the chat's armed state."""
        self._armed.discard(chat_id)

    async def fire(
        self,
        message: Message,
        prompt: str,
        cl: logging.Logger,
        react_to: Callable[[Message, str], Awaitable[None]],
        reply_with_agent: Callable[[Message, str, logging.Logger], Awaitable[None]],
    ) -> None:
        """Engage plan mode in the agent and run one turn with the prompt."""
        await self._gate.cancel_active_aq(message.chat.id)
        try:
            await self._agent.set_permission_mode(message.chat.id, "plan")
        except Exception as e:
            self._glog.exception(
                "[%s] set_permission_mode plan failed", self._bot_name
            )
            cl.exception("plan mode engage failed: %s", e)
            await send_md(
                message,
                self._tr.t("plan_mode_failed", error=type(e).__name__),
            )
            return
        cl.info("plan prompt=%r", prompt[:300])
        await send_md(message, self._tr.t("plan_mode_engaged"))
        await react_to(message, prompt)
        await reply_with_agent(message, prompt, cl)
