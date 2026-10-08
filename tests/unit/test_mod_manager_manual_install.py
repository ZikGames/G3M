"""Unit tests for ModManager manual-install handoff."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QObject

from services.mod.service import ModManager


class _Parent(QObject):
    pass


def test_manual_install_handoff_clears_finished_url_install_task(temp_dir, qapp):
    current_task = Mock()
    app_state = SimpleNamespace(
        is_installing=True,
        current_task=current_task,
        clear_current_task=Mock(side_effect=lambda: vars(app_state).__setitem__("current_task", None)),
    )
    parent = _Parent()
    manager = ModManager(app_state, Mock(), parent=parent)
    statuses = []
    manager.status_changed.connect(lambda message, color: statuses.append((message, color)))

    manager._on_manual_install_required(
        prepared_path=temp_dir,
        archive_path="archive.zip",
        temp_dir=temp_dir,
    )

    assert app_state.is_installing is False
    app_state.clear_current_task.assert_called_once()
    assert app_state.current_task is None
    assert statuses


def test_manual_install_error_cleans_temp_dir_if_feedback_fails(tmp_path, qapp):
    temp_dir = tmp_path / "manual"
    temp_dir.mkdir()
    app_state = SimpleNamespace(
        is_installing=True,
        current_task=Mock(),
        clear_current_task=Mock(),
    )
    parent = _Parent()
    vars(parent)["pizza_oven_conversion_presenter"] = Mock()
    vars(parent)["pizza_oven_conversion_presenter"].prompt_with_manual_options.side_effect = (
        RuntimeError("presenter failed")
    )
    vars(parent)["feedback_service"] = Mock()
    vars(parent)["feedback_service"].show_message.side_effect = RuntimeError("feedback failed")
    manager = ModManager(app_state, Mock(), parent=parent)

    manager._on_manual_install_required(
        prepared_path=str(temp_dir),
        archive_path="archive.zip",
        temp_dir=str(temp_dir),
    )

    assert not temp_dir.exists()


@pytest.mark.parametrize("accepted", [False, True])
def test_manual_cancel_does_not_reopen_setup_prompt(tmp_path, qapp, monkeypatch, accepted):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

    from presentation.pizza_oven_conversion_presenter import (
        PizzaOvenConversionPresenter,
    )
    from services.localization_service import tr
    from ui.dialogs.manual_install.dialog import ManualModInstallDialog

    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "README.txt").write_text("Guide", encoding="utf-8")
    parent = QWidget()
    vars(parent)["app_state"] = SimpleNamespace(local_config={}, mods_dir=str(tmp_path / "mods"))
    presenter = PizzaOvenConversionPresenter(vars(parent)["app_state"], Mock(), Mock(), Mock(), Mock(), parent)
    monkeypatch.setattr(presenter, "should_offer_conversion", lambda *_args: False)
    monkeypatch.setattr(presenter, "_safe_information", lambda *_args: None)
    prompt_exec = QMessageBox.exec
    manual_exec = QDialog.exec
    prompts = []

    def choose_manual(box):
        prompts.append(box)
        assert len(prompts) == 1, "Cancelling manual setup must finish the workflow"
        QTimer.singleShot(0, lambda: next(button for button in box.buttons() if button.text() == tr("ui.manual_install")).click())
        return prompt_exec(box)

    def close_manual(dialog):
        QTimer.singleShot(0, dialog.accept if accepted else dialog.reject)
        return manual_exec(dialog)

    monkeypatch.setattr(QMessageBox, "exec", choose_manual)
    monkeypatch.setattr(ManualModInstallDialog, "exec", close_manual)
    on_success = Mock()
    result = presenter.prompt_with_manual_options(
        parent, error_title="Setup", error_text="Configure files", informative_text="",
        prepared_path=str(prepared), source_file_path=None, temp_dir=str(prepared), on_success=on_success,
    )
    assert result is accepted
    assert len(prompts) == 1
    assert on_success.call_count == int(accepted)
    assert not prepared.exists()


def test_url_install_connects_real_worker_and_clears_task_on_failure(qapp, app_state, monkeypatch):
    from workers.install.url_install_worker import UrlInstallThread

    monkeypatch.setattr(UrlInstallThread, "start", lambda _worker: None)
    parent = _Parent()
    manager = ModManager(app_state, Mock(), parent=parent)
    manager.install_from_url("https://example.invalid/mod.zip")

    worker = app_state.current_task
    assert isinstance(worker, UrlInstallThread)
    assert app_state.is_installing
    worker.result_ready.emit(False, "download failed")
    assert not app_state.is_installing
    assert app_state.current_task is None
