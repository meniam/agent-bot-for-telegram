"""Questionnaire parsing and compact Telegram UI rendering."""

from src.i18n import Translator
from src.ui.questionnaire import (
    Question,
    Questionnaire,
    _keyboard,
    _poll_payload,
    _question_text,
    parse_questionnaire,
)


def test_parse_fenced_questionnaire() -> None:
    """A fenced questionnaire block parses into questions."""
    payload = """```bot_questionnaire
{
  "type": "questionnaire",
  "questions": [
    {
      "kind": "single_select",
      "question": "Pick one",
      "options": ["A", "B"]
    },
    {
      "kind": "text",
      "question": "Explain"
    }
  ]
}
```"""

    questionnaire = parse_questionnaire(payload)

    assert questionnaire is not None
    assert len(questionnaire.questions) == 2
    assert questionnaire.questions[0].kind == "single_select"
    assert questionnaire.questions[0].options == ("A", "B")
    assert questionnaire.questions[1].kind == "text"


def test_parse_raw_json_questionnaire() -> None:
    """A raw JSON questionnaire parses into questions."""
    payload = """
{
  "type": "questionnaire",
  "questions": [
    {
      "kind": "multi_select",
      "question": "Pick many",
      "options": ["A", "B", "C"]
    }
  ]
}
"""

    questionnaire = parse_questionnaire(payload)

    assert questionnaire is not None
    assert questionnaire.questions[0].kind == "multi_select"


def test_parse_accepts_zero_based_correct_options() -> None:
    """Quiz metadata parses as zero-based correct option indices."""
    payload = """
{
  "type": "questionnaire",
  "questions": [
    {
      "kind": "single_select",
      "question": "Pick one",
      "options": ["A", "B", "C"],
      "correct_options": [1]
    }
  ]
}
"""

    questionnaire = parse_questionnaire(payload)

    assert questionnaire is not None
    assert questionnaire.questions[0].correct_options == (1,)


def test_parse_rejects_invalid_correct_options() -> None:
    """Quiz metadata must point at existing options."""
    payload = """
{
  "type": "questionnaire",
  "questions": [
    {
      "kind": "single_select",
      "question": "Pick one",
      "options": ["A", "B"],
      "correct_options": [2]
    }
  ]
}
"""

    assert parse_questionnaire(payload) is None


def test_parse_ignores_normal_text() -> None:
    """Plain text is not parsed as a questionnaire."""
    assert parse_questionnaire("1. What is PHP?\n2. What is Composer?") is None


def test_parse_rejects_text_question_with_options() -> None:
    """A text question carrying options is rejected."""
    payload = """
{
  "type": "questionnaire",
  "questions": [
    {
      "kind": "text",
      "question": "Explain",
      "options": ["A", "B"]
    }
  ]
}
"""

    assert parse_questionnaire(payload) is None


def test_long_options_render_in_message_text_not_buttons() -> None:
    """Long option labels move to message text while buttons stay compact."""
    long_option = "A very long answer that would not fit inside a Telegram button"
    questionnaire = Questionnaire(
        questions=(
            Question(
                kind="single_select",
                question="Pick one",
                options=(long_option, "Short"),
            ),
        ),
    )
    tr = Translator("en")

    text = _question_text(tr, questionnaire, 0)
    keyboard = _keyboard("token", questionnaire, 0, {}, tr)

    assert long_option in text
    button_texts = [
        button.text
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert long_option not in button_texts
    assert button_texts[:2] == ["1", "2"]


def test_selected_option_is_marked_in_text_and_button() -> None:
    """A selected answer gets a compact marker in both visible places."""
    questionnaire = Questionnaire(
        questions=(
            Question(
                kind="multi_select",
                question="Pick many",
                options=("First", "Second"),
            ),
        ),
    )
    tr = Translator("en")

    text = _question_text(tr, questionnaire, 0, {1})
    keyboard = _keyboard("token", questionnaire, 0, {0: {1}}, tr)

    assert "✓ 2. Second" in text
    assert keyboard.inline_keyboard[0][1].text == "✓ 2"


def test_poll_payload_uses_quiz_when_correct_options_exist() -> None:
    """Native poll rendering uses Telegram quiz mode for graded questions."""
    questionnaire = Questionnaire(
        questions=(
            Question(
                kind="single_select",
                question="Pick one",
                options=("First", "Second"),
                correct_options=(1,),
            ),
        ),
    )

    payload = _poll_payload(Translator("en"), questionnaire, 0)

    assert payload["type"] == "quiz"
    assert payload["correct_option_ids"] == [1]
    assert payload["is_anonymous"] is False
    assert payload["allows_revoting"] is False


def test_poll_payload_uses_regular_poll_without_correct_options() -> None:
    """Native poll rendering avoids quiz mode when there is no right answer."""
    questionnaire = Questionnaire(
        questions=(
            Question(
                kind="multi_select",
                question="Pick many",
                options=("First", "Second"),
            ),
        ),
    )

    payload = _poll_payload(Translator("en"), questionnaire, 0)

    assert payload["type"] == "regular"
    assert "correct_option_ids" not in payload
    assert payload["allows_multiple_answers"] is True
