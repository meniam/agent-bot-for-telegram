"""Render structured agent questionnaires as Telegram UI."""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
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
_POLL_QUESTIONNAIRES: dict[str, PollQuestionnaireSession] = {}


@dataclass(slots=True, frozen=True)
class Question:
    """A single questionnaire question with its kind and any options."""

    kind: QuestionKind
    question: str
    options: tuple[str, ...] = ()
    correct_options: tuple[int, ...] = ()


@dataclass(slots=True, frozen=True)
class Questionnaire:
    """A validated set of 1-5 questions parsed from an agent payload."""

    questions: tuple[Question, ...]


@dataclass(slots=True)
class QuestionnaireSession:
    """Live state for a rendered questionnaire: position and selections."""

    questionnaire: Questionnaire
    chat_id: int
    message_id: int
    current_index: int = 0
    selected: dict[int, set[int]] = field(default_factory=dict)


@dataclass(slots=True)
class PollQuestionnaireSession:
    """Live state for a questionnaire rendered as native Telegram polls."""

    questionnaire: Questionnaire
    chat_id: int
    user_id: int | None
    poll_to_question: dict[str, int] = field(default_factory=dict)
    poll_message_ids: dict[str, int] = field(default_factory=dict)
    selected: dict[int, set[int]] = field(default_factory=dict)
    finalized: bool = False


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
        correct_options = _parse_correct_options(raw_q, len(clean_options), kind)
        if correct_options is None:
            return None
        questions.append(
            Question(
                kind=kind,
                question=question.strip(),
                options=clean_options,
                correct_options=correct_options,
            )
        )
    return Questionnaire(questions=tuple(questions))


def _parse_correct_options(
    raw_q: dict[str, Any],
    option_count: int,
    kind: object,
) -> tuple[int, ...] | None:
    """Validate optional zero-based correct option indices for native quizzes."""
    raw_correct = raw_q.get("correct_options", raw_q.get("correct_option_ids"))
    if raw_correct is None:
        return ()
    if not isinstance(raw_correct, list) or not raw_correct:
        return None
    correct: list[int] = []
    for raw_idx in raw_correct:
        if not isinstance(raw_idx, int):
            return None
        if raw_idx < 0 or raw_idx >= option_count:
            return None
        correct.append(raw_idx)
    unique = tuple(sorted(set(correct)))
    if kind == "single_select" and len(unique) != 1:
        return None
    return unique


def _extract_payload(text: str) -> str | None:
    """Pull the JSON body from a fenced block or a bare `{...}` answer."""
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
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
    """Render a questionnaire as native polls when possible, else inline UI."""
    if _can_render_as_polls(questionnaire):
        await _render_poll_questionnaire(message, questionnaire, t)
        return
    await _render_inline_questionnaire(message, questionnaire, t)


def _can_render_as_polls(questionnaire: Questionnaire) -> bool:
    """Return whether every question can be represented as a native poll."""
    return all(question.kind != "text" for question in questionnaire.questions)


async def _render_poll_questionnaire(
    message: Message,
    questionnaire: Questionnaire,
    t: Translator,
) -> None:
    """Send every select question as a native Telegram poll or quiz."""
    user_id = message.from_user.id if message.from_user is not None else None
    session = PollQuestionnaireSession(
        questionnaire=questionnaire,
        chat_id=message.chat.id,
        user_id=user_id,
    )
    for index, _question in enumerate(questionnaire.questions):
        payload = _poll_payload(t, questionnaire, index)
        sent = await message.answer_poll(**payload)
        if sent.poll is None:
            continue
        session.poll_to_question[sent.poll.id] = index
        session.poll_message_ids[sent.poll.id] = sent.message_id
        _POLL_QUESTIONNAIRES[sent.poll.id] = session


async def _render_inline_questionnaire(
    message: Message,
    questionnaire: Questionnaire,
    t: Translator,
) -> None:
    """Send the first inline question and register a live session keyed by a token."""
    token = secrets.token_hex(5)
    sent = await message.answer(
        _question_text(t, questionnaire, 0, set()),
        reply_markup=_keyboard(token, questionnaire, 0, {}, t),
        parse_mode=None,
    )
    _QUESTIONNAIRES[token] = QuestionnaireSession(
        questionnaire=questionnaire,
        chat_id=message.chat.id,
        message_id=sent.message_id,
    )


async def on_callback(callback: CallbackQuery, t: Translator) -> str | None:
    """Handle a `qq:` callback; return the agent summary once completed, else None."""
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
    if callback.bot is None:
        await callback.answer()
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
            text=_question_text(
                t,
                session.questionnaire,
                session.current_index,
                session.selected.get(session.current_index, set()),
            ),
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


async def on_poll_answer(
    poll_answer: PollAnswer,
    t: Translator,
) -> tuple[int, str, str, list[int]] | None:
    """Handle a native poll answer; return chat, summary, prompt, and poll messages."""
    session = _POLL_QUESTIONNAIRES.get(poll_answer.poll_id)
    if session is None or session.finalized:
        return None
    user = poll_answer.user
    if session.user_id is not None and (user is None or user.id != session.user_id):
        return None
    question_index = session.poll_to_question.get(poll_answer.poll_id)
    if question_index is None:
        return None
    session.selected[question_index] = set(poll_answer.option_ids)
    if _missing_answers(session):
        return None

    session.finalized = True
    for poll_id in session.poll_to_question:
        _POLL_QUESTIONNAIRES.pop(poll_id, None)
    return (
        session.chat_id,
        _summary_text(t, session),
        _agent_summary_text(session),
        list(session.poll_message_ids.values()),
    )


def _question_text(
    t: Translator,
    questionnaire: Questionnaire,
    index: int,
    selected: set[int] | None = None,
) -> str:
    """Format a question with full option text in the message body."""
    total = len(questionnaire.questions)
    question = questionnaire.questions[index]
    lines = [
        t.t(
            "questionnaire_question",
            index=index + 1,
            total=total,
            question=question.question,
        )
    ]
    option_lines = _option_lines(question, selected or set())
    if option_lines:
        lines.append("")
        lines.extend(option_lines)
    return "\n".join(lines)


def _option_lines(question: Question, selected: set[int]) -> list[str]:
    """Render full option labels as numbered message-body lines."""
    lines: list[str] = []
    for opt_idx, option in enumerate(question.options):
        prefix = f"{opt_idx + 1}."
        if opt_idx in selected:
            prefix = f"✓ {prefix}"
        lines.append(f"{prefix} {option}")
    return lines


def _option_button_text(opt_idx: int, selected: set[int]) -> str:
    """Return a compact button label for one option number."""
    if opt_idx in selected:
        return f"✓ {opt_idx + 1}"
    return str(opt_idx + 1)


def _button_rows(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Split compact option buttons into Telegram keyboard rows."""
    width = 4
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


def _poll_payload(
    t: Translator,
    questionnaire: Questionnaire,
    index: int,
) -> dict[str, Any]:
    """Build kwargs for ``Message.answer_poll`` for one questionnaire item."""
    question = questionnaire.questions[index]
    is_quiz = bool(question.correct_options)
    payload: dict[str, Any] = {
        "question": _clip(
            t.t(
                "questionnaire_question",
                index=index + 1,
                total=len(questionnaire.questions),
                question=question.question,
            ),
            300,
        ),
        "options": [_clip(option, 100) for option in question.options],
        "is_anonymous": False,
        "type": "quiz" if is_quiz else "regular",
        "allows_multiple_answers": question.kind == "multi_select",
        "allows_revoting": False,
        "shuffle_options": False,
    }
    if is_quiz:
        payload["correct_option_ids"] = list(question.correct_options)
    return payload


def _clip(text: str, limit: int) -> str:
    """Trim Telegram poll text fields to their Bot API length limit."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: max(0, limit - 3)].rstrip() + "..."


def _keyboard(
    token: str,
    questionnaire: Questionnaire,
    index: int,
    selected: dict[int, set[int]],
    t: Translator,
) -> InlineKeyboardMarkup:
    """Build the option, navigation, and done buttons for the current question."""
    question = questionnaire.questions[index]
    rows: list[list[InlineKeyboardButton]] = []
    option_buttons: list[InlineKeyboardButton] = []
    selected_for_question = selected.get(index, set())
    for opt_idx, _option in enumerate(question.options):
        option_buttons.append(
            InlineKeyboardButton(
                text=_option_button_text(opt_idx, selected_for_question),
                callback_data=f"qq:{token}:opt:{opt_idx}",
            )
        )
    rows.extend(_button_rows(option_buttons))

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


def _missing_answers(session: QuestionnaireSession | PollQuestionnaireSession) -> list[int]:
    """Return the 1-based indices of select questions with no selection yet."""
    missing: list[int] = []
    for idx, question in enumerate(session.questionnaire.questions):
        if question.kind == "text":
            continue
        if not session.selected.get(idx):
            missing.append(idx + 1)
    return missing


def _summary_text(
    t: Translator,
    session: QuestionnaireSession | PollQuestionnaireSession,
) -> str:
    """Render the user-facing completion summary of selected answers."""
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


def _agent_summary_text(session: QuestionnaireSession | PollQuestionnaireSession) -> str:
    """Render the answers as a prompt fed back to the agent to continue."""
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
