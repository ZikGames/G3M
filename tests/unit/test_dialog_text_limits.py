"""Dialog text and name lists stay short however many items an operation produces."""

from services.localization_service import tr
from utils.process_utils import (
    DIALOG_MAX_CHARS,
    DIALOG_MAX_LINES,
    join_limited,
    limit_dialog_text,
)


def test_short_text_is_untouched():
    text = "one\ntwo\nthree"
    assert limit_dialog_text(text) == text


def test_many_lines_are_cut_with_a_pointer_to_the_log():
    lines = [f"Operation {i}: target will be created: C:/Games/mus/{i}.ogg" for i in range(500)]
    result = limit_dialog_text("\n".join(lines))

    assert len(result.splitlines()) <= DIALOG_MAX_LINES
    assert len(result) <= DIALOG_MAX_CHARS
    assert result.splitlines()[0] == lines[0]
    assert result.splitlines()[-1] == tr("ui.more_in_log", count=500 - (len(result.splitlines()) - 1))


def test_one_huge_line_is_cut():
    result = limit_dialog_text("x" * 100_000)
    assert len(result) <= DIALOG_MAX_CHARS
    assert result.endswith(tr("ui.more_in_log", count=1))


def test_limiting_twice_changes_nothing():
    once = limit_dialog_text("\n".join(f"line {i}" for i in range(300)))
    assert limit_dialog_text(once) == once
    huge = limit_dialog_text("y" * 50_000)
    assert limit_dialog_text(huge) == huge


def test_name_lists_keep_only_the_first_few():
    names = [f"mod{i}" for i in range(40)]
    result = join_limited(names)
    assert result.startswith("mod0, mod1")
    assert "mod10" not in result
    assert result.endswith(tr("ui.more_in_log", count=30))
    assert join_limited(["a", "b"]) == "a, b"


def test_the_scrolling_warning_dialog_keeps_a_longer_list():
    from services.mod_operation_support import _bounded_warning_lines

    result = _bounded_warning_lines([f"Operation {i}: target will be created" for i in range(600)])

    assert len(result.splitlines()) == 100
    assert result.splitlines()[-2] == "Operation 98: target will be created"


def test_message_box_limits_follow_the_screen_height(qapp, monkeypatch):
    from types import SimpleNamespace

    from PyQt6.QtGui import QGuiApplication

    from ui.common.feedback import FeedbackManager

    manager = FeedbackManager(None)
    heights = {}
    for name, height in {"small": 600, "full_hd": 1040, "uhd": 2100}.items():
        screen = SimpleNamespace(availableGeometry=lambda height=height: SimpleNamespace(height=lambda: height))
        monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda screen=screen: screen))
        heights[name] = manager._text_limits()[0]

    assert heights["small"] == 15
    assert 15 <= heights["full_hd"] < heights["uhd"]
