"""Render structured agent questionnaires as Telegram UI."""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

if TYPE_CHECKING:
    from ..i18n import Translator

log = logging.getLogger(__name__)

QuestionKind = Literal["single_select", "multi_select", "text"]

_FENCE_RE = re.compile(
    r"```bot_questionnaire\s*(?P<body>\{.*?\})\s*```",
    re.DOTALL,
)
_QUESTIONNAIRES: dict[str, QuestionnaireSession] = {}


@dataclass(slots=True, frozen=True)
class Question:
    kind: QuestionKind
    question: str
    options: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class Questionnaire:
    questions: tuple[Question, ...]


@dataclass(slots=True)
class QuestionnaireSession:
    questionnaire: Questionnaire
    chat_id: int
    message_id: int
    current_index: int = 0
    selected: dict[int, set[int]] = field(default_factory=dict)


def parse_questionnaire(text: str) -> Questionnaire | None:
    """Return a questionnaire when the whole answer is a bot UI payload."""
    raw = _extract_payload(text)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "questionnaire":
        return None
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 5:
        return None

    questions: list[Question] = []
    for raw_q in raw_questions:
        if not isinstance(raw_q, dict):
            return None
        kind = raw_q.get("kind")
        if kind not in {"single_select", "multi_select", "text"}:
            return None
        question = raw_q.get("question")
        if not isinstance(question, str) or not question.strip():
            return None
        options = raw_q.get("options")
        if kind == "text":
            if options is not None:
                return None
            questions.append(Question(kind=kind, question=question.strip()))
            continue
        if not isinstance(options, list) or not 2 <= len(options) <= 8:
            return None
        clean_options = tuple(str(opt).strip() for opt in options if str(opt).strip())
        if len(clean_options) != len(options):
            return None
        questions.append(
            Question(
                kind=kind,
                question=question.strip(),
                options=clean_options,
            )
        )
    return Questionnaire(questions=tuple(questions))


def _extract_payload(text: str) -> str | None:
    stripped = text.strip()
    match = _FENCE_RE.fullmatch(stripped)
    if match is not None:
        return match.group("body")
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    return None


async def render_questionnaire(
    message: Message,
    questionnaire: Questionnaire,
    t: Translator,
) -> None:
    token = secrets.token_hex(5)
    sent = await message.answer(
        _question_text(t, questionnaire, 0),
        reply_markup=_keyboard(token, questionnaire, 0, {}, t),
        parse_mode=None,
    )
    _QUESTIONNAIRES[token] = QuestionnaireSession(
        questionnaire=questionnaire,
        chat_id=message.chat.id,
        message_id=sent.message_id,
    )


async def on_callback(callback: CallbackQuery, t: Translator) -> str | None:
    data = callback.data or ""
    if not data.startswith("qq:"):
        return None
    try:
        _, token, action, value = data.split(":", 3)
    except ValueError:
        await callback.answer()
        return None

    session = _QUESTIONNAIRES.get(token)
    if session is None:
        await callback.answer(t.t("callback_outdated"), show_alert=False)
        return None
    if callback.message is None or callback.message.chat.id != session.chat_id:
        await callback.answer(t.t("unauthorized_callback"), show_alert=True)
        return None

    current = session.current_index
    question = session.questionnaire.questions[current]
    if action == "nav":
        if value == "prev":
            session.current_index = max(current - 1, 0)
        elif value == "next":
            session.current_index = min(
                current + 1,
                len(session.questionnaire.questions) - 1,
            )
        else:
            await callback.answer()
            return None
    elif action == "done":
        missing = _missing_answers(session)
        if missing:
            await callback.answer(
                t.t("questionnaire_missing", questions=", ".join(map(str, missing))),
                show_alert=True,
            )
            return None
        agent_summary = _agent_summary_text(session)
        try:
            await callback.bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.message_id,
                text=_summary_text(t, session),
                reply_markup=None,
                parse_mode=None,
            )
        except Exception:
            log.debug("could not finalize questionnaire", exc_info=True)
        _QUESTIONNAIRES.pop(token, None)
        await callback.answer(t.t("questionnaire_done_toast"))
        return agent_summary
    elif action == "opt":
        try:
            opt_idx = int(value)
        except ValueError:
            await callback.answer()
            return None
        if opt_idx < 0 or opt_idx >= len(question.options):
            await callback.answer()
            return None
        selected = session.selected.setdefault(current, set())
        if question.kind == "single_select":
            selected.clear()
            selected.add(opt_idx)
            session.current_index = min(
                current + 1,
                len(session.questionnaire.questions) - 1,
            )
        elif question.kind == "multi_select":
            if opt_idx in selected:
                selected.remove(opt_idx)
            else:
                selected.add(opt_idx)
    else:
        await callback.answer()
        return None

    try:
        await callback.bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.message_id,
            text=_question_text(t, session.questionnaire, session.current_index),
            reply_markup=_keyboard(
                token,
                session.questionnaire,
                session.current_index,
                session.selected,
                t,
            ),
            parse_mode=None,
        )
    except Exception:
        log.debug("could not redraw questionnaire keyboard", exc_info=True)
    await callback.answer(t.t("callback_received"))
    return None


def _question_text(t: Translator, questionnaire: Questionnaire, index: int) -> str:
    total = len(questionnaire.questions)
    question = questionnaire.questions[index]
    return t.t(
        "questionnaire_question",
        index=index + 1,
        total=total,
        question=question.question,
    )


def _keyboard(
    token: str,
    questionnaire: Questionnaire,
    index: int,
    selected: dict[int, set[int]],
    t: Translator,
) -> InlineKeyboardMarkup:
    question = questionnaire.questions[index]
    rows: list[list[InlineKeyboardButton]] = []
    for opt_idx, option in enumerate(question.options):
        prefix = "✓ " if opt_idx in selected.get(index, set()) else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{option}"[:64],
                    callback_data=f"qq:{token}:opt:{opt_idx}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀",
                callback_data=f"qq:{token}:nav:prev",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{index + 1}/{len(questionnaire.questions)}",
            callback_data=f"qq:{token}:nav:noop",
        )
    )
    if index < len(questionnaire.questions) - 1:
        nav.append(
            InlineKeyboardButton(
                text="▶",
                callback_data=f"qq:{token}:nav:next",
            )
        )
    rows.append(nav)
    if index == len(questionnaire.questions) - 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t.t("questionnaire_done_button"),
                    callback_data=f"qq:{token}:done:0",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _missing_answers(session: QuestionnaireSession) -> list[int]:
    missing: list[int] = []
    for idx, question in enumerate(session.questionnaire.questions):
        if question.kind == "text":
            continue
        if not session.selected.get(idx):
            missing.append(idx + 1)
    return missing


def _summary_text(t: Translator, session: QuestionnaireSession) -> str:
    lines = [t.t("questionnaire_done")]
    for idx, question in enumerate(session.questionnaire.questions):
        if question.kind == "text":
            continue
        answers = [
            question.options[opt_idx]
            for opt_idx in sorted(session.selected.get(idx, set()))
            if 0 <= opt_idx < len(question.options)
        ]
        answer = ", ".join(answers) if answers else "—"
        lines.append("")
        lines.append(
            t.t(
                "questionnaire_answer",
                index=idx + 1,
                question=question.question,
                answer=answer,
            )
        )
    return "\n".join(lines)


def _agent_summary_text(session: QuestionnaireSession) -> str:
    lines = ["User completed the Telegram questionnaire:"]
    for idx, question in enumerate(session.questionnaire.questions):
        if question.kind == "text":
            continue
        answers = [
            question.options[opt_idx]
            for opt_idx in sorted(session.selected.get(idx, set()))
            if 0 <= opt_idx < len(question.options)
        ]
        answer = ", ".join(answers) if answers else "(no answer)"
        lines.append("")
        lines.append(f"{idx + 1}. {question.question}")
        lines.append(f"Answer: {answer}")
    lines.append("")
    lines.append("Continue from these answers. If this was a quiz, grade them and explain briefly.")
    return "\n".join(lines)
