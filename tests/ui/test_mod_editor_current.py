"""Regression tests for the current-format mod editor."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractItemView, QTabWidget, QWidget

from ui.dialogs.mod_editor import dialog as dialog_module
from ui.dialogs.mod_editor.dialog import _OPERATION_TYPE_ORDER, ModEditorDialog
from ui.utils.thread_lifetime import retire_qthread
from utils.mod.config import validate_mod_config
from utils.mod.hashing import sha256_path
from utils.mod.operation_plan import ModPathContext


def test_editor_validation_dialog_has_a_widget_parent(qtbot, tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    qtbot.addWidget(parent)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    qtbot.addWidget(dialog)
    warnings = []

    def warning(parent_widget, title, message):
        warnings.append((parent_widget, title, message))

    monkeypatch.setattr(ModEditorDialog, "_safe_warning", staticmethod(warning))

    for homepage, clear_name in [("", True), ("invalid", False), ("http://[", False)]:
        warnings.clear()
        dialog.name_edit.setText("" if clear_name else "Editor Test")
        dialog.homepage_edit.setText(homepage)
        assert not dialog._valid_for_save()
        assert len(warnings) == 1
        assert warnings[0][0] is dialog


def test_editor_save_failure_shows_error_without_closing(qtbot, tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    qtbot.addWidget(parent)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    qtbot.addWidget(dialog)
    errors = []

    def fail_write(*_args):
        raise OSError("disk full")

    def critical(parent_widget, title, message):
        errors.append((parent_widget, title, message))

    monkeypatch.setattr(dialog_module, "write_mod_config", fail_write)
    monkeypatch.setattr(ModEditorDialog, "_safe_critical", staticmethod(critical))

    dialog._save()

    assert dialog.result() != dialog.DialogCode.Accepted
    assert len(errors) == 1
    assert errors[0][0] is dialog


def test_editor_cannot_save_before_requested_hashes_complete(qtbot, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    qtbot.addWidget(parent)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    qtbot.addWidget(dialog)
    key = ((1, 0), "source_hash")
    dialog._hash_pending[key] = 1

    assert not dialog._valid_for_save()
    assert not dialog._save_button.isEnabled()
    dialog._hash_ready(*key, 1, "", "unreadable source")
    assert not dialog._valid_for_save()
    assert not dialog._save_button.isEnabled()
    dialog._cancel_hash(key)
    assert key not in dialog._hash_errors
    dialog._hash_pending[key] = 2
    dialog._hash_ready(*key, 2, "sha256:" + "0" * 64, "")
    assert dialog._valid_for_save()
    assert dialog._save_button.isEnabled()


def test_editor_invalid_relation_url_can_be_validated_without_crashing():
    assert ModEditorDialog._normalize_relation_id("https://[") == "https://["


def test_editor_hash_finishes_safely_after_parent_deletion(qtbot, tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("readme", encoding="utf-8")
    started, finish = threading.Event(), threading.Event()

    def hash_file(_target):
        started.set()
        assert finish.wait(5)
        return "sha256:" + "0" * 64

    monkeypatch.setattr(dialog_module, "resolved_sha256", hash_file)
    parent = _parent(tmp_path)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._source_hash_box.setChecked(True)
    thread = next(iter(dialog._hash_threads))
    try:
        assert started.wait(2)
        sip.delete(parent)
        assert sip.isdeleted(dialog)
        assert not sip.isdeleted(thread)
    finally:
        finish.set()
        assert thread.wait(2000)
    retire_qthread(thread)
    qtbot.waitUntil(lambda: sip.isdeleted(thread))


def test_editor_export_cannot_overwrite_mod_sources(qtbot, tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    source = folder / "mod_config.json"
    source.write_text("original", encoding="utf-8")
    parent = _parent(tmp_path)
    qtbot.addWidget(parent)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    qtbot.addWidget(dialog)
    errors = []
    monkeypatch.setattr(dialog_module, "get_save_file_name", lambda *_args: (str(source), ""))
    monkeypatch.setattr(ModEditorDialog, "_safe_critical", staticmethod(lambda parent_widget, title, message: errors.append(parent_widget)))

    dialog._export()

    assert source.read_text(encoding="utf-8") == "original"
    assert errors == [dialog]


def _parent(tmp_path):
    parent = QWidget()
    parent.app_state = SimpleNamespace(local_config={}, mods_dir=str(tmp_path / "mods"))
    parent.mod_service = Mock(get_mod_folder_path=Mock())
    return parent


def _config(folder):
    return {
        "config_version": "2.0.0",
        "id": "editor-test",
        "name": "Editor Test",
        "version": "1.0.0",
        "authors": ["Author"],
        "game": "undertale",
        "files": [
            {"source": "${mod_path}/README.md", "type": "info"},
            {
                "Core": [
                    {
                        "source": "${mod_path}/replacement.txt",
                        "target": "${game_path}/replacement.txt",
                        "type": "overwrite",
                    }
                ]
            },
        ],
        "folder_path": str(folder),
    }


def test_current_editor_uses_tabs_and_one_ordered_tree(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))

    assert dialog.width() == 1240
    assert not hasattr(dialog, "file_tabs")
    assert not hasattr(dialog, "_info_files_list")
    assert dialog._tree.topLevelItemCount() == 2
    group = dialog._tree.topLevelItem(1)
    assert group.text(0) == "Core"
    assert group.childCount() == 1
    assert [dialog._tabs.tabText(index) for index in range(dialog._tabs.count())] == [
        "Metadata",
        "Compatibility",
        "Files",
        "Placeholders",
        "Help",
    ]
    assert dialog._tabs.currentIndex() == 0
    assert isinstance(dialog._tabs, QTabWidget)
    assert dialog._operation_splitter.count() == 2
    assert dialog._tree.columnCount() == 3
    assert dialog._tree.parentWidget().layout().contentsMargins().left() == 2
    assert (
        dialog._custom_placeholders_tree.parentWidget().layout().contentsMargins().left()
        == 20
    )
    assert dialog._tree.currentItem() == dialog._tree.topLevelItem(0)
    assert dialog._tree.topLevelItem(0).text(0) == "1"
    assert dialog._tree.topLevelItem(0).text(2) == "${mod_path}/README.md"
    assert not dialog._tree.topLevelItem(0).icon(1).isNull()
    assert dialog._type.currentData() == "info"
    dialog._tree.setCurrentItem(group.child(0))
    qapp.processEvents()
    assert dialog._type.currentData() == "overwrite"
    assert dialog._type.currentText() == "Overwrite"
    assert dialog._source.text() == "${mod_path}/replacement.txt"
    assert dialog._target.text() == "${game_path}/replacement.txt"
    assert [dialog._type.itemData(index) for index in range(dialog._type.count())] == list(
        _OPERATION_TYPE_ORDER
    )
    assert not hasattr(dialog, "_move_up_button")
    assert not hasattr(dialog, "_move_down_button")


def test_current_editor_moves_entries_into_groups_and_normalizes_gamebanana_links(
    qapp, tmp_path
):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))

    assert dialog._move_entry(
        (0,), (1,), QAbstractItemView.DropIndicatorPosition.OnItem
    )
    assert list(dialog._operation_files[0]) == ["Core"]
    assert [entry["type"] for entry in dialog._operation_files[0]["Core"]] == [
        "overwrite",
        "info",
    ]
    dialog._add_relation("dependencies")
    dialog._relation_id_edits["dependencies"].setText(
        "https://gamebanana.com/wips/103749"
    )
    dialog._save_relation("dependencies")

    assert dialog._config()["dependencies"] == ["gb_wip_103749"]
    assert dialog._normalize_relation_id("https://gamebanana.com/mods/123") == (
        "gb_mod_123"
    )
    assert dialog._normalize_relation_id("https://GAMEBANANA.COM/WIPS/103749/") == (
        "gb_wip_103749"
    )
    assert dialog._normalize_relation_id("https://example.com/wips/103749") == (
        "https://example.com/wips/103749"
    )


def test_current_editor_groups_operations_dropped_onto_each_other(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    source = dialog._operation_files[0]
    target = dialog._operation_files[1]["Core"][0]

    with patch.object(dialog_module.QInputDialog, "getText", return_value=("Bundle", True)):
        assert dialog._move_entry(
            (0,), (1, 0), QAbstractItemView.DropIndicatorPosition.OnItem
        )

    group = dialog._operation_files[0]["Core"][0]
    assert group == {"Bundle": [target, source]}
    assert not validate_mod_config(dialog._config())


def test_current_editor_groups_sibling_operations_without_losing_either(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    source = dialog._operation_files[0]
    target = dialog._operation_files[1]["Core"][0]
    dialog._operation_files = [source, target]
    dialog._refresh_tree()

    with patch.object(dialog_module.QInputDialog, "getText", return_value=("Bundle", True)):
        assert dialog._move_entry(
            (0,), (1,), QAbstractItemView.DropIndicatorPosition.OnItem
        )

    assert dialog._operation_files == [{"Bundle": [target, source]}]
    assert not validate_mod_config(dialog._config())


def test_current_editor_cancels_operation_grouping_without_mutation(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    before = deepcopy(dialog._operation_files)

    with patch.object(dialog_module.QInputDialog, "getText", return_value=("", False)):
        assert not dialog._move_entry(
            (0,), (1, 0), QAbstractItemView.DropIndicatorPosition.OnItem
        )

    assert dialog._operation_files == before


def test_current_editor_nests_groups_dropped_onto_groups(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._operation_files = [
        {"Destination": []},
        {"Source": [{"source": "${mod_path}/source.md", "type": "info"}]},
    ]
    dialog._refresh_tree()

    assert dialog._move_entry(
        (1,), (0,), QAbstractItemView.DropIndicatorPosition.OnItem
    )
    assert dialog._operation_files == [
        {
            "Destination": [
                {"Source": [{"source": "${mod_path}/source.md", "type": "info"}]}
            ]
        }
    ]
    assert not validate_mod_config(dialog._config())


def test_current_editor_drag_moves_preserve_entries_across_nested_targets(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._operation_files = [
        {
            "A": [
                {"source": "a", "target": "a", "type": "overwrite"},
                {
                    "Nested": [
                        {"source": "n", "target": "n", "type": "overwrite"}
                    ]
                },
            ]
        },
        {"B": [{"source": "b", "target": "b", "type": "overwrite"}]},
    ]
    dialog._refresh_tree()
    def leaves():
        return [
            leaf
            for _groups, leaf in dialog_module.iter_mod_config_leaves(
                dialog._operation_files
            )
        ]
    before = {id(leaf) for leaf in leaves()}

    assert dialog._move_entry(
        (0, 0), (1,), QAbstractItemView.DropIndicatorPosition.OnItem
    )
    assert dialog._move_entry(
        (1, 1), (1, 0), QAbstractItemView.DropIndicatorPosition.AboveItem
    )
    assert dialog._move_entry(
        (0, 0), (1,), QAbstractItemView.DropIndicatorPosition.OnItem
    )
    assert dialog._move_entry(
        (1, 1), None, QAbstractItemView.DropIndicatorPosition.OnViewport
    )
    assert not dialog._move_entry(
        (1, 1), (1, 1, 0), QAbstractItemView.DropIndicatorPosition.OnItem
    )
    assert {id(leaf) for leaf in leaves()} == before


def test_current_editor_uses_custom_drag_without_tree_internal_move(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    tree = dialog._tree
    tree.setCurrentItem(tree.topLevelItem(0))
    before = list(dialog._operation_files)

    with patch.object(dialog_module, "QDrag") as drag:
        tree.startDrag(Qt.DropAction.MoveAction)

    drag.assert_called_once_with(tree)
    drag.return_value.exec.assert_called_once_with(Qt.DropAction.MoveAction)
    assert tree.dragDropMode() == QAbstractItemView.DragDropMode.DragDrop
    assert tree._drag_path is None
    assert dialog._operation_files == before


def test_current_editor_uses_human_localized_validation_messages(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(1).child(0))
    dialog._type.setCurrentIndex(dialog._type.findData("extract"))

    assert dialog._validation.text().startswith("Target:")
    assert "files[" not in dialog._validation.text()


def test_current_editor_localization_keys_exist_in_every_bundled_language():
    keys = {
        "mod_editor_tab_metadata",
        "mod_editor_tab_compatibility",
        "mod_editor_tab_files",
        "mod_editor_tab_placeholders",
        "mod_editor_tab_help",
        "mod_editor_authors",
        "authors_label",
        "mod_authors",
        "mod_editor_compatibility_hint",
        "mod_editor_relation_mod_id",
        "mod_editor_relation_mod_id_placeholder",
        "mod_editor_relation_order",
        "mod_editor_relation_order_none",
        "mod_editor_relation_order_before",
        "mod_editor_relation_order_after",
        "mod_editor_relation_order_before_step",
        "mod_editor_relation_order_after_step",
        "mod_editor_relation_order_before_priority",
        "mod_editor_relation_order_after_priority",
        "mod_editor_validation_format",
        "mod_editor_validation_configuration",
        "mod_editor_validation_target_directory",
        "mod_editor_validation_target_unsupported",
        "mod_editor_validation_source_file",
        "mod_editor_validation_target_required",
        "mod_editor_validation_source_required",
        "mod_editor_validation_not_used",
        "mod_editor_validation_invalid",
        "mod_editor_group_name_taken",
        "mod_editor_group_depth_limit",
        "mod_editor_custom_placeholders_hint",
        "mod_editor_placeholder_name",
        "mod_editor_placeholder_path",
        "mod_editor_placeholder_name_placeholder",
        "mod_editor_placeholder_path_placeholder",
        "mod_editor_placeholder_name_taken",
        "mod_editor_placeholder_name_reserved",
        "mod_editor_placeholder_name_invalid",
        "mod_editor_help_metadata_title",
        "mod_editor_help_metadata_body",
        "mod_editor_help_placeholders_title",
        "mod_editor_help_placeholders_body",
        "mod_editor_help_placeholder_examples",
        "mod_editor_help_placeholder_example",
        "mod_editor_help_path_unavailable",
        "mod_editor_help_custom_placeholders_title",
        "mod_editor_help_custom_placeholders_body",
        "mod_editor_help_operations_title",
        "mod_editor_help_operations_body",
        "mod_editor_help_order_title",
        "mod_editor_help_order_body",
        "mod_editor_help_compatibility_title",
        "mod_editor_help_compatibility_body",
    }
    language_dir = Path(dialog_module.__file__).parents[3] / "assets" / "lang"

    for language_file in language_dir.glob("lang_*.json"):
        values = json.loads(language_file.read_text(encoding="utf-8"))["ui"]
        assert not keys - values.keys(), language_file.name
        assert all(
            "—" not in value and ";" not in value
            for key, value in values.items()
            if key.startswith("mod_editor_help_") and isinstance(value, str)
        )
        assert "snake_case" not in values["mod_editor_help_custom_placeholders_body"]
        assert "assets.zip/" in values["mod_editor_help_placeholders_body"]


def test_current_config_authors_are_shown_by_the_local_mod_model(tmp_path):
    from models.mod_models import LocalModInfo

    config = _config(tmp_path)

    assert LocalModInfo.from_dict(config).authors == ["Author"]


def test_current_editor_hashes_are_read_only_and_recalculate(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    first, second = folder / "README.md", folder / "SECOND.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))
    dialog._source_hash_box.setChecked(True)

    wait = cast(Callable[[int], None], QTest.qWait)
    for _ in range(30):
        wait(10)
        qapp.processEvents()
        if dialog._operation_files[0].get("source_hash"):
            break
    assert dialog._source_hash.isReadOnly()
    assert dialog._operation_files[0]["source_hash"] == sha256_path(first)
    assert dialog._target_hash_box.isHidden()

    dialog._source.setText("${mod_path}/SECOND.md")
    dialog._source.editingFinished.emit()
    for _ in range(30):
        wait(10)
        qapp.processEvents()
        if dialog._operation_files[0].get("source_hash") == sha256_path(second):
            break
    assert dialog._operation_files[0]["source_hash"] == sha256_path(second)


def test_current_editor_operation_icon_colors_follow_the_main_text_theme(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.app_state.local_config["custom_main_text_color"] = "#e63737"
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))

    assert dialog._operation_icon_color("patch") == "#e63737"
    assert dialog._operation_icon_color("soft-overwrite") != "#e63737"
    assert dialog._operation_icon_color("hard-extract") != "#e63737"
    assert dialog._operation_icon_color("soft-overwrite") != dialog._operation_icon_color(
        "hard-extract"
    )
    previous_icon_key = dialog._operation_icons["patch"].cacheKey()
    parent.app_state.local_config["custom_main_text_color"] = "#357ee6"
    dialog.apply_theme()

    assert dialog._operation_icon_color("patch") == "#357ee6"
    assert dialog._operation_icon_color("soft-overwrite") != "#357ee6"
    assert dialog._operation_icons["patch"].cacheKey() != previous_icon_key


def test_current_editor_preserves_custom_placeholders_and_marks_the_invalid_path(
    qapp, tmp_path
):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    config = _config(folder)
    config["placeholders"] = {"saves_path": "${user_path}/AppData/Local/Example"}
    config["icon"] = "https://images.example.com/icon.png"
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=config)

    assert dialog._config()["placeholders"] == config["placeholders"]
    assert dialog._config()["icon"] == config["icon"]
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(1).child(0))
    dialog._source.setText("./replacement.txt")
    dialog._source.editingFinished.emit()
    qapp.processEvents()

    assert "#d9534f" in dialog._source.styleSheet()
    assert dialog._source.toolTip()


def test_current_editor_adds_edits_and_removes_custom_placeholders(qapp, tmp_path):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    config = _config(folder)
    config["placeholders"] = {"saves_path": "${user_path}/AppData/Local/Example"}
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=config)

    assert dialog._custom_placeholders_tree.topLevelItemCount() == 1
    assert dialog._custom_placeholder_name.text() == "saves_path"
    dialog._add_custom_placeholder()
    dialog._custom_placeholder_name.setText("AssetFolder")
    dialog._custom_placeholder_path.setText("${mod_path}/assets")
    dialog._save_custom_placeholder()

    assert dialog._config()["placeholders"] == {
        "saves_path": "${user_path}/AppData/Local/Example",
        "AssetFolder": "${mod_path}/assets",
    }
    assert not validate_mod_config(dialog._config())
    dialog._remove_custom_placeholder()

    assert dialog._config()["placeholders"] == {
        "saves_path": "${user_path}/AppData/Local/Example"
    }


def test_current_editor_rejects_reserved_and_duplicate_custom_placeholder_names(
    qapp, tmp_path, monkeypatch
):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    config = _config(folder)
    config["placeholders"] = {"assets": "${mod_path}/assets"}
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=config)
    warning = Mock()
    monkeypatch.setattr(dialog, "_safe_warning", warning)

    dialog._add_custom_placeholder()
    dialog._custom_placeholder_name.setText("MOD_PATH")
    dialog._save_custom_placeholder()

    assert set(dialog._config()["placeholders"]) == {"assets", "placeholder"}
    assert dialog._custom_placeholder_name.text() == "placeholder"
    warning.assert_called_once()

    dialog._custom_placeholder_name.setText("ASSETS")
    dialog._save_custom_placeholder()

    assert set(dialog._config()["placeholders"]) == {"assets", "placeholder"}
    assert warning.call_count == 2


def test_current_editor_keeps_operation_form_aligned_and_has_sectioned_help(
    qapp, tmp_path, monkeypatch
):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))

    assert len({label.width() for label in dialog._form_labels.values()}) == 1
    monkeypatch.setattr(
        dialog,
        "_context",
        lambda: ModPathContext.create(
            mod_path=folder,
            game_path=tmp_path / "game",
            game_data_path=tmp_path / "game" / "data",
            user_path=tmp_path / "user",
        ),
    )
    dialog.relocalize_ui()

    assert [dialog._help_tabs.tabText(index) for index in range(dialog._help_tabs.count())] == [
        "Metadata",
        "Placeholders",
        "Custom placeholders",
        "Operations",
        "Processing order",
        "Compatibility && hashes",
    ]
    assert "${game_data_path}" in dialog._help_sections["placeholders"].toPlainText()
    assert str(folder) in dialog._help_sections["placeholders"].toPlainText()
    assert "assets.zip/" in dialog._help_sections["placeholders"].toPlainText()


def test_current_editor_browse_uses_portable_roots(qapp, tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "editor"
    game = tmp_path / "game"
    source = folder / "files" / "replacement.txt"
    target = game / "mods"
    source.parent.mkdir(parents=True)
    target.mkdir(parents=True)
    source.write_text("content", encoding="utf-8")
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(1).child(0))
    monkeypatch.setattr(dialog_module, "get_open_file_name", lambda *_: (str(source), ""))
    monkeypatch.setattr(dialog_module, "get_existing_directory", lambda *_: str(target))
    monkeypatch.setattr(
        dialog,
        "_context",
        lambda: ModPathContext.create(
            mod_path=folder,
            game_path=game,
            game_data_path=game / "data",
            user_path=tmp_path / "user",
        ),
    )

    dialog._browse_source()
    dialog._browse_target()
    qapp.processEvents()

    assert dialog._source.text() == "${mod_path}/files/replacement.txt"
    assert dialog._target.text() == "${game_path}/mods/"


def test_current_editor_warns_for_a_custom_target(qapp, tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "editor"
    folder.mkdir(parents=True)
    custom = tmp_path / "outside"
    custom.mkdir()
    parent = _parent(tmp_path)
    parent.mod_service.get_mod_folder_path.return_value = str(folder)
    dialog = ModEditorDialog(parent, is_creating=False, mod_data=_config(folder))
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(1).child(0))
    warning = Mock()
    monkeypatch.setattr(dialog_module, "get_existing_directory", lambda *_: str(custom))
    monkeypatch.setattr(dialog, "_safe_warning", warning)
    monkeypatch.setattr(
        dialog,
        "_context",
        lambda: ModPathContext.create(
            mod_path=folder,
            game_path=tmp_path / "game",
            game_data_path=tmp_path / "game-data",
            user_path=tmp_path / "user",
        ),
    )

    dialog._browse_target()

    assert dialog._target.text().endswith("/outside/")
    warning.assert_called_once()
