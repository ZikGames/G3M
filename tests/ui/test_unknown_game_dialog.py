"""Handling a G3M mod made for a game that is not set up."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

import ui.dialogs.unknown_game_dialog as module
from services.game_registry_service import GameRegistryValidationError
from ui.dialogs.custom_game_dialog import CustomGameDialog
from ui.dialogs.unknown_game_dialog import (
    GAME_READY,
    MANUAL_INSTALL,
    WorkerGameResolver,
    resolve_unknown_game,
)


@pytest.fixture
def host(qapp, app_state):
    widget = QWidget()
    vars(widget)["app_state"] = app_state
    vars(widget)["game_registry_service"] = Mock()
    yield widget
    widget.deleteLater()


def _click(monkeypatch, which):
    """Make the question box answer with its add or manual button, or with neither."""
    keys = {"add": "games.mod_unknown_add", "manual": "games.mod_unknown_manual"}

    def exec_(box):
        wanted = module.tr(keys[which]) if which else None
        box.chosen = next((button for button in box.buttons() if button.text() == wanted), None)
        return 0

    monkeypatch.setattr(module.DynamicMessageBox, "exec", exec_)
    monkeypatch.setattr(module.DynamicMessageBox, "clickedButton", lambda box: box.chosen)


def test_a_known_game_needs_no_question(host, monkeypatch):
    monkeypatch.setattr(module.DynamicMessageBox, "exec", lambda _box: pytest.fail("asked"))

    assert resolve_unknown_game(host, "deltarune", "Pack") == GAME_READY


def test_manual_install_is_the_fallback_without_a_registry(qapp):
    assert resolve_unknown_game(QWidget(), "mystery", "Pack") == MANUAL_INSTALL


def test_an_owner_that_is_not_a_qt_object_falls_back_to_manual_install():
    assert resolve_unknown_game(SimpleNamespace(), "mystery", "Pack") == MANUAL_INSTALL


def test_the_user_can_pick_manual_install(host, monkeypatch):
    _click(monkeypatch, "manual")

    assert resolve_unknown_game(host, "mystery", "Pack") == MANUAL_INSTALL
    host.game_registry_service.create_custom_game.assert_not_called()


def test_cancelling_the_question_cancels_the_install(host, monkeypatch):
    _click(monkeypatch, None)

    assert resolve_unknown_game(host, "mystery", "Pack") is None


def test_adding_the_game_uses_the_mod_id_and_the_gamebanana_game(host, monkeypatch):
    _click(monkeypatch, "add")
    seen = {}

    def accept(dialog):
        seen.update(
            game_id=dialog.game_id_edit.text(),
            gamebanana=dialog.gamebanana_id_edit.text(),
            read_only=dialog.game_id_edit.isReadOnly(),
        )
        dialog.display_name_edit.setText("Mystery")
        dialog.primary_executable_edit.setText("mystery.exe")
        dialog.data_file_name_edit.setText("data.win")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CustomGameDialog, "exec", accept)

    assert resolve_unknown_game(host, "mystery-game", "Pack", gamebanana_id=424242) == GAME_READY

    assert seen == {"game_id": "mystery-game", "gamebanana": "424242", "read_only": True}
    created = host.game_registry_service.create_custom_game
    created.assert_called_once()
    assert created.call_args.kwargs["game_id"] == "mystery-game"
    assert created.call_args.kwargs["primary_executable"] == "mystery.exe"
    assert created.call_args.kwargs["gamebanana_id"] == "424242"


def test_a_supported_gamebanana_game_is_not_prefilled(host, monkeypatch):
    _click(monkeypatch, "add")
    seen = []

    def reject(dialog):
        seen.append(dialog.gamebanana_id_edit.text())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(CustomGameDialog, "exec", reject)
    supported = next(iter(module.get_gamebanana_reverse_map()))

    assert resolve_unknown_game(host, "mystery", "Pack", gamebanana_id=supported) is None
    assert seen == [""]


def test_a_rejected_game_is_edited_again_and_cancelling_cancels(host, monkeypatch):
    _click(monkeypatch, "add")
    answers = iter([QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected])
    monkeypatch.setattr(CustomGameDialog, "exec", lambda _dialog: next(answers))
    host.game_registry_service.create_custom_game.side_effect = GameRegistryValidationError(
        "games.validation_name_unique"
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

    assert resolve_unknown_game(host, "mystery", "Pack") is None
    assert len(warnings) == 1


def test_a_worker_thread_waits_for_the_game_to_be_settled(host, monkeypatch, qapp):
    resolver = WorkerGameResolver(host)
    monkeypatch.setattr(module, "resolve_unknown_game", lambda *_args: GAME_READY)
    answers = []
    worker = threading.Thread(target=lambda: answers.append(resolver.resolve("mystery", "Pack", lambda: 7)))

    worker.start()
    while worker.is_alive():
        qapp.processEvents()
        worker.join(0.01)

    assert answers == [GAME_READY]


def test_an_id_the_registry_would_reject_falls_back_to_manual_install(host, monkeypatch):
    host.game_registry_service.is_valid_new_game_id.return_value = False
    monkeypatch.setattr(module.DynamicMessageBox, "exec", lambda _box: pytest.fail("asked"))

    assert resolve_unknown_game(host, "mystery", "Pack") == MANUAL_INSTALL


def test_custom_game_dialog_offers_an_optional_game_id(qapp, app_state):
    dialog = CustomGameDialog(app_state)
    dialog.display_name_edit.setText("Hotline")

    assert dialog.minimumWidth() >= 915
    assert dialog.game_id_edit.placeholderText() == module.tr("games.game_id_placeholder")
    assert dialog.values()["game_id"] == ""
    dialog.game_id_edit.setText(" Hotline-Game ")
    assert dialog.values()["game_id"] == "hotline-game"


def test_custom_game_dialog_fixes_the_id_a_mod_names(qapp, app_state):
    dialog = CustomGameDialog(app_state, game_id="mystery")

    assert dialog.game_id_edit.isReadOnly()
    assert "game_id" not in dialog.values()
