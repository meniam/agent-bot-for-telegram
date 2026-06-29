"""AskUserQuestion compact Telegram UI helpers."""

from aiogram.types import InlineKeyboardButton

from src.infra.interactions.ask_user_question import _button_rows, _option_lines


def test_option_lines_keep_full_answer_labels() -> None:
    """Full option labels render in the message body."""
    long_option = "A very long answer that would not fit inside a Telegram button"

    lines = _option_lines([{"label": long_option}, {"label": "Short"}], set())

    assert lines == [f"1. {long_option}", "2. Short"]


def test_selected_option_lines_are_marked() -> None:
    """Selected multi-select answers render with a visible marker."""
    lines = _option_lines([{"label": "First"}, {"label": "Second"}], {1})

    assert lines == ["1. First", "✓ 2. Second"]


def test_button_rows_use_compact_four_column_chunks() -> None:
    """Compact option buttons split into predictable rows."""
    buttons = [
        InlineKeyboardButton(text=str(i), callback_data=f"cb:{i}")
        for i in range(1, 6)
    ]

    rows = _button_rows(buttons)

    assert [[button.text for button in row] for row in rows] == [
        ["1", "2", "3", "4"],
        ["5"],
    ]
