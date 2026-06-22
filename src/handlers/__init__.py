"""Handler registration entry point.

`register_all(dp, ctx, custom_commands)` wires every aiogram handler in the
correct order. The order matters: `F.text` is greedy, so all exact `Command`
filters (including the user-defined ones) MUST be registered before it.
"""

from aiogram import Dispatcher

from ..infra.commands import CommandDef
from . import (
    basic,
    custom,
    plan,
    questionnaire,
    selectors,
    sessions,
    tasks,
    text,
    uploads,
    voice,
)


def register_all(
    dp: Dispatcher,
    custom_commands: list[CommandDef],
) -> None:
    # Exact Command(...) filters — order between these is irrelevant.
    selectors.register(dp)
    basic.register(dp)
    sessions.register(dp)
    tasks.register(dp)
    plan.register(dp)
    questionnaire.register(dp)
    # User-defined commands MUST come before F.text so `/<name>` does not
    # fall through to the generic text handler.
    custom.register(dp, custom_commands)
    # Catch-alls.
    text.register(dp)
    voice.register(dp)
    uploads.register(dp)
