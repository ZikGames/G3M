"""Dialogs that choose which saved mod versions are exported or installed."""

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from ui.dialogs.mod.version_choice_dialog import (
    ExportVersionsDialog,
    InstallVersionsDialog,
    WorkerVersionChoice,
    choose_export_versions,
    choose_install_versions,
)
from ui.widgets.mod.installed_mod_widget import InstalledModWidget
from utils.mod.version_package import VersionSelection

VERSIONS = [{"name": "1.0", "size": 10, "mtime": 0}, {"name": "2.0", "size": 20, "mtime": 0}]


@pytest.fixture
def host(qapp, app_state):
    widget = QWidget()
    vars(widget)["app_state"] = app_state
    yield widget
    widget.deleteLater()


def test_export_defaults_to_only_the_active_version(host):
    dialog = ExportVersionsDialog("Pack", "3.0", VERSIONS, host)

    assert dialog.only_active.isChecked()
    assert dialog.selected_versions() == frozenset()
    assert not any(row.isEnabled() for row in dialog.rows[1:])


def test_export_lists_every_version_once_the_switch_is_off(host):
    dialog = ExportVersionsDialog("Pack", "3.0", VERSIONS, host)

    dialog.only_active.setChecked(False)

    assert dialog.selected_versions() == {"1.0", "2.0"}
    dialog.rows[1].check.setChecked(False)
    assert dialog.selected_versions() == {"2.0"}


def test_export_with_nothing_ticked_is_the_same_as_only_active(host):
    dialog = ExportVersionsDialog("Pack", "3.0", VERSIONS, host)
    dialog.only_active.setChecked(False)

    for row in dialog.rows[1:]:
        row.check.setChecked(False)

    assert dialog.selected_versions() == frozenset()


def test_install_keeps_everything_with_the_default_active(host):
    dialog = InstallVersionsDialog("Pack", "3.0", VERSIONS, host)

    assert dialog.selection() == VersionSelection(frozenset({"1.0", "2.0"}), True, None)


def test_install_can_pick_another_active_version(host):
    dialog = InstallVersionsDialog("Pack", "3.0", VERSIONS, host)

    radio = dialog.rows[2].radio
    assert radio is not None
    radio.setChecked(True)

    assert dialog.selection().active == "2.0"


def test_unticking_the_active_version_moves_the_choice(host):
    dialog = InstallVersionsDialog("Pack", "3.0", VERSIONS, host)

    dialog.rows[0].check.setChecked(False)

    selection = dialog.selection()
    assert (selection.keep_root, selection.active) == (False, "1.0")


def test_install_needs_at_least_one_version(host):
    dialog = InstallVersionsDialog("Pack", "3.0", VERSIONS, host)

    for row in dialog.rows:
        row.check.setChecked(False)

    assert not dialog.accept_button.isEnabled()
    dialog.rows[1].check.setChecked(True)
    assert dialog.accept_button.isEnabled()


def test_dialogs_follow_a_language_change(host):
    dialog = InstallVersionsDialog("Pack", "3.0", VERSIONS, host)
    from services.localization_service import localization_service

    previous = localization_service.get_current_language()
    try:
        localization_service.load_language("ru")
        dialog.relocalize_ui()
        assert dialog.accept_button.text() == "Установить"
        assert dialog.rows[0].radio is not None
        assert dialog.rows[0].radio.text() == "Активная"
    finally:
        localization_service.load_language(previous)


def test_a_worker_thread_waits_for_the_answer(qapp, host, monkeypatch):
    chooser = WorkerVersionChoice(host)
    expected = VersionSelection(frozenset({"1.0"}), True, None)
    monkeypatch.setattr(
        "ui.dialogs.mod.version_choice_dialog.choose_install_versions",
        lambda *_args: expected,
    )
    answers = []
    worker = threading.Thread(target=lambda: answers.append(chooser.ask("Pack", "3.0", VERSIONS)))

    worker.start()
    while worker.is_alive():
        qapp.processEvents()
        worker.join(0.01)

    assert answers == [expected]


@pytest.mark.parametrize(
    ("mode", "exported", "installed"),
    [("active", frozenset(), frozenset()), ("all", {"1.0", "2.0"}, {"1.0", "2.0"})],
)
def test_settings_decide_without_a_dialog(host, app_state, monkeypatch, mode, exported, installed):
    app_state.local_config.update(mod_versions_export_mode=mode, mod_versions_import_mode=mode)
    monkeypatch.setattr(ExportVersionsDialog, "exec", lambda self: pytest.fail("dialog shown"))
    monkeypatch.setattr(InstallVersionsDialog, "exec", lambda self: pytest.fail("dialog shown"))

    assert choose_export_versions(host, "Pack", "3.0", VERSIONS) == exported
    assert choose_install_versions(host, "Pack", "3.0", VERSIONS) == VersionSelection(frozenset(installed), True, None)


def test_unknown_setting_falls_back_to_asking(host, app_state, monkeypatch):
    app_state.local_config["mod_versions_export_mode"] = "bogus"
    monkeypatch.setattr(ExportVersionsDialog, "exec", lambda self: 0)

    assert choose_export_versions(host, "Pack", "3.0", VERSIONS) is None


def test_setting_is_found_through_the_parent_chain(host, app_state, monkeypatch):
    app_state.local_config["mod_versions_export_mode"] = "all"
    child = QObject(host)

    assert choose_export_versions(child, "Pack", "3.0", VERSIONS) == {"1.0", "2.0"}


def _card(monkeypatch, buttons):
    status = []
    card: Any = SimpleNamespace(
        _chosen_versions=None,
        _CHOICE_LIFETIME=60.0,
        mod_data=object(),
        parent_app=SimpleNamespace(_update_localized_status=lambda *args: status.append(args)),
    )
    monkeypatch.setattr(QApplication, "mouseButtons", staticmethod(lambda: buttons))
    return card, status


def test_choice_made_after_the_mouse_was_released_is_used_by_the_next_drag(monkeypatch):
    card, status = _card(monkeypatch, Qt.MouseButton.NoButton)
    controller = Mock(choose_export_versions=Mock(return_value=frozenset({"1.0"})))

    assert InstalledModWidget._take_chosen_versions(card, controller) is None
    assert status
    assert InstalledModWidget._take_chosen_versions(card, controller) == {"1.0"}
    assert controller.choose_export_versions.call_count == 1
    assert card._chosen_versions is None


def test_choice_made_with_the_button_still_held_starts_the_drag(monkeypatch):
    card, status = _card(monkeypatch, Qt.MouseButton.LeftButton)
    controller = Mock(choose_export_versions=Mock(return_value=frozenset()))

    assert InstalledModWidget._take_chosen_versions(card, controller) == frozenset()
    assert not status


def test_cancelled_choice_does_not_start_a_drag(monkeypatch):
    card, _ = _card(monkeypatch, Qt.MouseButton.LeftButton)

    assert InstalledModWidget._take_chosen_versions(card, Mock(choose_export_versions=Mock(return_value=None))) is None
