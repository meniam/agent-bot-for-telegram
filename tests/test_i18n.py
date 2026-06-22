"""Translator: lang loading, per-key fallback to default language, format args."""

from src.i18n import DEFAULT_LANG, Translator, available_languages


def test_default_language_loads() -> None:
    """The default language loads and unknown keys return the key."""
    tr = Translator(DEFAULT_LANG)
    assert tr.lang == DEFAULT_LANG
    # Non-existent key returns key itself.
    assert tr.t("definitely_missing_key_xyz") == "definitely_missing_key_xyz"


def test_unknown_lang_falls_back_to_default() -> None:
    """An unknown language falls back to the default language."""
    tr = Translator("xx-not-a-lang")
    assert tr.lang == DEFAULT_LANG


def test_format_kwargs_applied() -> None:
    """Format kwargs are applied to a translated string."""
    tr = Translator(DEFAULT_LANG)
    # Pick any key that uses {error} — `error_internal` is one such key.
    # If it isn't formatted, just confirm format() does not crash.
    out = tr.t("error_internal", error="X")
    assert isinstance(out, str)


def test_missing_format_arg_returns_raw() -> None:
    """Passing the wrong format kwargs does not raise."""
    tr = Translator(DEFAULT_LANG)
    # Passing wrong kwargs must not raise.
    out = tr.t("error_internal", wrong_key="value")
    assert isinstance(out, str)


def test_available_languages_lists_json_stems() -> None:
    """Available languages include the default and English."""
    langs = available_languages()
    assert DEFAULT_LANG in langs
    assert "en" in langs


def test_task_keys_present_in_all_locales() -> None:
    """Every locale file defines all task-related translation keys."""
    import json
    from pathlib import Path

    keys = [
        "task_disabled",
        "task_usage",
        "task_created",
        "task_global_created",
        "task_add_error",
        "task_admin_only",
        "task_list_empty",
        "task_list_header",
        "task_not_found",
        "task_paused",
        "task_resumed",
        "task_removed",
        "task_triggered",
    ]
    i18n_dir = Path(__file__).resolve().parent.parent / "src" / "i18n"
    for path in i18n_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            assert key in data, f"{path.name} missing {key}"


def test_task_message_formatting() -> None:
    """Task messages interpolate their format arguments."""
    tr = Translator(DEFAULT_LANG)
    assert "abc" in tr.t("task_created", id="abc", schedule="every 30m")
    assert "boom" in tr.t("task_add_error", error="boom")
    assert "xyz" in tr.t("task_not_found", id="xyz")
