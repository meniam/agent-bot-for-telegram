from src.ui.questionnaire import parse_questionnaire


def test_parse_fenced_questionnaire() -> None:
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


def test_parse_ignores_normal_text() -> None:
    assert parse_questionnaire("1. What is PHP?\n2. What is Composer?") is None


def test_parse_rejects_text_question_with_options() -> None:
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
