import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QScrollArea, QWidget

from models.game_version_models import GameVersionRecord
from ui.dialogs.game.create_version_dialog import CreateVersionDialog
from ui.dialogs.game.versions_dialog import GameVersionsDialog, _VersionRecordWidget
from ui.dialogs.mod.versions_dialog import _VersionItemWidget


def test_game_snapshot_resolves_selected_profile_and_patch_steps(qapp, app_state, tmp_path):
    profile_root = tmp_path / "Other"
    data = {
        "selected_game_type": "undertale",
        "used_mods_undertale": {"undertale": ["alpha", "beta", "gamma"]},
        "mod_steps_undertale": {"undertale": [["beta", "alpha"], ["gamma"]]},
    }
    for mod_id in ("alpha", "beta", "gamma"):
        folder = profile_root / mod_id
        folder.mkdir(parents=True)
        (folder / "mod_config.json").write_text(json.dumps({
            "config_version": "2.0.0", "id": mod_id, "name": mod_id,
            "version": "1.0.0", "authors": [], "game": "undertale", "files": [],
        }), encoding="utf-8")
    parent = QWidget()
    parent.feedback_service = Mock()
    parent.settings_service = Mock()
    manager = Mock()
    manager.records_for_game.return_value = []
    profile_service = SimpleNamespace(_read_profile=lambda _name: data, _profile_dir=lambda _name: profile_root)
    app_state.local_config = {"active_profile": "Default", "merge_code": True}
    dialog = GameVersionsDialog(manager, app_state, parent=parent)

    selections, state, mods = dialog._resolve_profile_mods("Other", "undertale", profile_service)

    assert [[entry["id"] for entry in step] for step in selections["undertale"]] == [["beta", "alpha"], ["gamma"]]
    assert state.game_mode.game_id == "undertale"
    assert state.local_config["active_profile"] == "Other"
    assert state.local_config["merge_code"] is True
    assert mods.get_mod_folder_path("alpha") == str(profile_root / "alpha")
    assert app_state.local_config == {"active_profile": "Default", "merge_code": True}
    data["used_mods_undertale"]["undertale"].append("missing")
    with pytest.raises(ValueError, match="Selected mod is unavailable"):
        dialog._resolve_profile_mods("Other", "undertale", profile_service)
    data["used_mods_undertale"]["undertale"] = [{}]
    with pytest.raises(ValueError, match="Invalid mod selections"):
        dialog._resolve_profile_mods("Other", "undertale", profile_service)


def test_create_version_requires_name_and_preserves_profile(qtbot, app_state):
    dialog = CreateVersionDialog("DELTARUNE", app_state, ["Music"])
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog._ok_button.isEnabled()
    assert dialog._name_label.buddy() is dialog._name_input
    assert not dialog._name_label.text().startswith("[")
    dialog._name_input.setText("   ")
    assert not dialog._ok_button.isEnabled()
    qtbot.keyClick(dialog._name_input, Qt.Key.Key_Return)
    assert dialog.isVisible()
    dialog._name_input.setText("Chapter One")
    assert dialog._ok_button.isEnabled()
    dialog._profile_combo.setCurrentIndex(1)
    assert dialog._profile_label.buddy() is dialog._profile_combo
    qtbot.keyClick(dialog._name_input, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.version_name == "Chapter One"
    assert dialog.selected_profile == "Music"


@pytest.mark.parametrize("kind", ["mod", "game"])
def test_long_version_name_keeps_actions_inside_viewport(qtbot, app_state, kind):
    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    scroll.setWidgetResizable(True)
    scroll.resize(500, 380)
    name = "ChapterOne" * 30
    if kind == "mod":
        row = _VersionItemWidget({"name": name})
        buttons = [row._switch_btn, row._delete_btn]
    else:
        manager = Mock()
        manager.is_busy.return_value = False
        row = _VersionRecordWidget(
            GameVersionRecord(archive_path=name + ".zip"), manager, app_state
        )
        buttons = [row._apply_btn, row._export_btn, row._delete_btn]
    row.setFont(QFont("Arial", 16))
    scroll.setWidget(row)
    scroll.show()
    qtbot.waitUntil(lambda: row.isVisible())
    assert row.width() <= scroll.viewport().width()
    assert row._name_label.wordWrap()
    for button in buttons:
        assert button.visibleRegion().boundingRect() == button.rect()
