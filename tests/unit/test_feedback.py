"""Unit tests for test feedback."""

import html
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest


def _make_message_box_stub():
    box = SimpleNamespace()
    box.Icon = SimpleNamespace(Question=object())
    box.StandardButton = SimpleNamespace(Yes=1, No=2)
    box.setIcon = Mock()
    box.setWindowTitle = Mock()
    box.setTextFormat = Mock()
    box.setText = Mock(side_effect=lambda value: vars(box).__setitem__("text", value))
    box.setStandardButtons = Mock()
    box.setDefaultButton = Mock()
    box.exec = Mock(return_value=box.StandardButton.Yes)
    box.text = ""
    box.set_localized_title = lambda key, **kwargs: box.setWindowTitle(box.translator(key, **kwargs))
    box.localize = lambda setter, key, **kwargs: setter(box.translator(key, **kwargs))
    box.localize_text = lambda control, key, **kwargs: control
    box.add_localized_button = lambda key, role: box.addButton(box.translator(key), role)
    factory = Mock(return_value=box)
    factory.Icon = box.Icon
    factory.StandardButton = box.StandardButton
    return factory, box


def test_ask_question_keeps_html_details(monkeypatch, qapp):
    from ui.common import feedback as feedback_module
    from ui.common.feedback import FeedbackManager

    factory, box = _make_message_box_stub()
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)
    manager = FeedbackManager()

    result = manager.ask_question(
        "status.update_available",
        "status.update_available",
        "<b>New launcher version</b><br>Line 2",
        default_yes=True,
        details_is_html=True,
    )

    assert result is True
    assert box.text.endswith("<b>New launcher version</b><br>Line 2")
    box.setTextFormat.assert_called_once_with(feedback_module.Qt.TextFormat.RichText)


def test_closing_dependency_dialog_without_resolve_button_cancels(monkeypatch, qapp):
    from ui.common import feedback as feedback_module

    factory, box = _make_message_box_stub()
    factory.ButtonRole = SimpleNamespace(AcceptRole=1, ActionRole=2, RejectRole=3)
    box.addButton = Mock(side_effect=[object(), object()])
    box.clickedButton = Mock(return_value=None)
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)

    assert feedback_module.FeedbackManager().ask_dependency_resolution(
        "Missing dependencies", can_activate=False, can_download=False,
    ) == "cancel"


def test_ask_question_escapes_plain_details(monkeypatch, qapp):
    from ui.common import feedback as feedback_module
    from ui.common.feedback import FeedbackManager

    factory, box = _make_message_box_stub()
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)
    manager = FeedbackManager()

    result = manager.ask_question(
        "status.update_available",
        "status.update_available",
        "<b>New launcher version</b><br>Line 2",
        True,
    )

    assert result is True
    assert html.escape("<b>New launcher version</b><br>Line 2", quote=False) in box.text


def test_ask_text_question_uses_rich_text_message_box(monkeypatch, qapp):
    from ui.common import feedback as feedback_module
    from ui.common.feedback import FeedbackManager

    factory, box = _make_message_box_stub()
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)
    manager = FeedbackManager()

    assert manager.ask_text_question("Confirm", "Use <mod>?", default_yes=True)

    box.setIcon.assert_called_once_with(factory.Icon.Question)
    box.setWindowTitle.assert_called_once_with("Confirm")
    box.setTextFormat.assert_called_once_with(feedback_module.Qt.TextFormat.RichText)
    assert box.text == "Use &lt;mod&gt;?"
    box.setStandardButtons.assert_called_once_with(
        factory.StandardButton.Yes | factory.StandardButton.No
    )
    box.setDefaultButton.assert_called_once_with(factory.StandardButton.Yes)


def test_show_message_does_not_escape_plain_apostrophes_to_entities(monkeypatch, qapp):
    from ui.common import feedback as feedback_module
    from ui.common.feedback import FeedbackManager

    factory, box = _make_message_box_stub()
    factory.Icon.Critical = object()
    factory.Icon.Warning = object()
    factory.Icon.Information = object()
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)
    manager = FeedbackManager(
        tr_func=lambda key, **kwargs: {
            "dialogs.warning": "Warning",
            "errors.mod_no_files": "Mod '{mod_name}' has no files to install.",
        }.get(key, str(key)).format(**kwargs)
    )

    manager.show_message(
        "warning", "errors.mod_no_files", mod_name="CoolMod"
    )

    assert "&#x27;" not in box.text
    assert box.text == "Mod 'CoolMod' has no files to install."


def test_feedback_manager_scoped_translator_localizes_titles_and_messages(
    monkeypatch, qapp
):
    from ui.common import feedback as feedback_module
    from ui.common.feedback import FeedbackManager

    factory, box = _make_message_box_stub()
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)
    manager = FeedbackManager(
        tr_func=lambda key, **_kwargs: {
            "dialogs.delete_save": "Delete save?",
            "dialogs.delete_save_confirmation": "Delete permanently?",
        }.get(key, f"[{key}]")
    )
    scoped = manager.scoped(
        lambda key, **_kwargs: {
            "dialogs.delete_save": "Delete save?",
            "dialogs.delete_save_confirmation": "Delete permanently?",
        }.get(key, f"[{key}]")
    )

    result = scoped.ask_question(
        "dialogs.delete_save",
        "dialogs.delete_save_confirmation",
    )

    assert result is True
    box.setWindowTitle.assert_called_once_with("Delete save?")
    assert "Delete permanently?" in box.text


@pytest.mark.parametrize("action", ["continue", "cancel", "report"])
def test_long_patching_warning_keeps_actions_on_screen(monkeypatch, qapp, qtbot, action):
    from PyQt6.QtCore import QRect, QTimer
    from PyQt6.QtGui import QScreen
    from PyQt6.QtWidgets import QMessageBox, QPushButton, QScrollArea

    from services.localization_service import tr
    from ui.common import feedback as module
    from ui.common.dialog_theme import DynamicDialog

    class SmallScreenDialog(DynamicDialog):
        def screen(self):
            return cast(QScreen, SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 800, 600)))

    monkeypatch.setattr(module, "QDialog", SmallScreenDialog)
    errors = []

    def interact():
        dialog = qapp.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(10, interact)
            return
        try:
            qtbot.addWidget(dialog)
            assert dialog.height() <= 520
            assert dialog.width() <= 760
            scroll = dialog.findChild(QScrollArea)
            assert scroll.verticalScrollBar().maximum() > 0
            assert "line 999" in scroll.widget().text()
            for button in dialog.findChildren(QPushButton):
                assert dialog.rect().contains(button.rect().translated(button.mapTo(dialog, button.rect().topLeft())))
            key = {"continue": "dialogs.patching_warning.continue_button", "cancel": "dialogs.patching_warning.cancel_button", "report": "dialogs.conflicts.open_report"}[action]
            next(button for button in dialog.findChildren(QPushButton) if button.text() == tr(key)).click()
        except Exception as error:
            errors.append(error)
        finally:
            if dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, interact)
    manager = module.FeedbackManager()
    result, disabled = manager._exec_patching_warning_dialog(
        "Warning", "<br>".join(f"Report line {index}" for index in range(1000)),
        QMessageBox.Icon.Warning, True, True,
    )
    assert not errors, errors
    assert result == action
    assert not disabled
