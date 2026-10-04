"""UI tests for test dialogs."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PyQt6.QtCore import QMimeData, QModelIndex, Qt, QUrl
from PyQt6.QtGui import QDropEvent, QTextCursor, QTextDocument
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QWidget

EXPECTED_DIALOG_WIDTH = 1145


@dataclass(frozen=True)
class _GameEntry:
    id: str
    display_name: str


def _close_dialog(qapp, dialog) -> None:
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


class TestImportDialog:
    """Tests for dialogs."""
    def test_import_dialog_creation(self, qapp, feedback_service):
        """Checks that importing dialog creation."""
        from services.localization_service import tr
        from ui.dialogs.import_dialog import ImportDialog
        dialog = ImportDialog(None, feedback_service, 'mods')
        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert dialog.windowTitle() == tr("mods.import_mods")

    def test_import_dialog_localizations_never_show_raw_keys(self, qapp, feedback_service):
        """Checks that import dialogs resolve visible localization keys for every shipped language."""
        from services.localization_service import localization_service
        from ui.dialogs.import_dialog import ImportDialog

        original_language = localization_service.get_current_language()
        import_types = ("mods", "themes", "game_versions", "mod_versions")

        try:
            for language_code in localization_service.get_available_languages():
                assert localization_service.load_language(language_code)
                for import_type in import_types:
                    dialog = ImportDialog(None, feedback_service, import_type)
                    labels = [label.text() for label in dialog.findChildren(QLabel)]
                    buttons = [button.text() for button in dialog.findChildren(QPushButton)]
                    visible_texts = [dialog.windowTitle(), dialog.url_input.placeholderText(), *labels, *buttons]

                    assert all(text and not text.startswith("[") for text in visible_texts)
                    _close_dialog(qapp, dialog)
        finally:
            localization_service.load_language(original_language)

    def test_empty_url_feedback_failure_keeps_dialog_open(self, qapp):
        """Checks empty URL warning failure does not accept or crash import dialog."""
        from ui.dialogs.import_dialog import ImportDialog

        feedback_service = Mock()
        feedback_service.show_message.side_effect = RuntimeError("toast deleted")
        dialog = ImportDialog(None, feedback_service, "mods")

        dialog._import_from_url()

        assert dialog.selected_url is None
        assert dialog.import_method is None
        feedback_service.show_message.assert_called_once()
        _close_dialog(qapp, dialog)


class TestGameBananaFilePickerDialog:
    """Tests for dialogs."""
    def test_file_picker_dialog_creation(self, qapp):
        """Checks that file picker dialog creation."""
        from ui.dialogs.file_picker_dialog import GameBananaFilePickerDialog
        dialog = GameBananaFilePickerDialog(None, [], 'Test Mod')
        assert dialog is not None
        assert isinstance(dialog, QDialog)


class TestCreateModpackDialog:
    """Tests for dialogs."""
    def test_create_modpack_dialog_creation(self, qapp, app_state):
        """Checks that creating modpack dialog creation."""
        from ui.dialogs.mod.pack_create_dialog import CreateModpackDialog
        dialog = CreateModpackDialog(app_state, None)
        assert dialog is not None
        assert isinstance(dialog, QDialog)


class TestConflictsDialog:
    """Tests for dialogs."""
    def test_conflicts_dialog_creation(self, qapp, temp_dir):
        """Checks that conflictsing dialog creation."""
        import os

        from ui.dialogs.conflicts_dialog import ConflictsDialog
        report_path = os.path.join(temp_dir, 'test_report.md')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write('## Merge Report\n\nTotal conflicts: 2\nAuto-resolved: 1\n')
        dialog = ConflictsDialog(report_path, None)
        assert dialog is not None
        assert isinstance(dialog, QDialog)

    def test_conflicts_dialog_missing_report_uses_localized_message(
        self, qapp, temp_dir, monkeypatch
    ):
        from services.localization_service import tr
        from ui.dialogs.conflicts_dialog import ConflictsDialog

        report_path = os.path.join(temp_dir, "missing_report.md")
        dialog = ConflictsDialog(report_path, None)
        calls = []
        monkeypatch.setattr(
            "ui.dialogs.conflicts_dialog.QMessageBox.information",
            lambda *args: calls.append(args),
        )

        dialog._open_report_file()

        assert calls
        assert calls[0][1] == tr("dialogs.conflicts.title")
        assert calls[0][2] == tr("errors.file_not_found", path=report_path)
        _close_dialog(qapp, dialog)

    def test_conflicts_dialog_missing_report_ignores_broken_info_dialog(
        self, qapp, temp_dir, monkeypatch
    ):
        from ui.dialogs.conflicts_dialog import ConflictsDialog

        report_path = os.path.join(temp_dir, "missing_report.md")
        dialog = ConflictsDialog(report_path, None)
        monkeypatch.setattr(
            "ui.dialogs.conflicts_dialog.QMessageBox.information",
            Mock(side_effect=RuntimeError("dialog already deleted")),
        )

        dialog._open_report_file()

        _close_dialog(qapp, dialog)


class TestPluginDetailsDialog:
    def test_plugin_update_button_is_after_delete_and_runs_callback(
        self, qapp, tmp_path
    ):
        from models.plugin_models import InstalledPluginRecord, PluginManifest
        from services.localization_service import tr
        from ui.dialogs.plugin_details_dialog import PluginDetailsDialog

        manifest = PluginManifest(
            config_version=1,
            id="sample_plugin",
            name="Sample",
            description="Sample plugin",
            author="Author",
            version="1.0.0",
            entry="plugin.py",
        )
        plugin = InstalledPluginRecord(manifest=manifest, path=str(tmp_path))
        updated = []
        dialog = PluginDetailsDialog(
            plugin,
            runtime_service=Mock(get_settings_widget=Mock(return_value=None)),
            state_service=Mock(),
            app_state=SimpleNamespace(local_config={}),
            can_update=True,
            on_update=updated.append,
        )

        button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
        delete_index = button_texts.index(tr("plugins.details_delete"))
        update_index = button_texts.index(tr("plugins.details_update"))
        assert update_index == delete_index + 1

        dialog.findChildren(QPushButton)[update_index].click()

        assert updated == ["sample_plugin"]
        _close_dialog(qapp, dialog)

    def test_plugin_delete_confirmation_failure_is_ignored(
        self, qapp, tmp_path, monkeypatch
    ):
        from PyQt6.QtWidgets import QMessageBox

        from models.plugin_models import InstalledPluginRecord, PluginManifest
        from ui.dialogs.plugin_details_dialog import PluginDetailsDialog

        manifest = PluginManifest(
            config_version=1,
            id="sample_plugin",
            name="Sample",
            description="Sample plugin",
            author="Author",
            version="1.0.0",
            entry="plugin.py",
        )
        plugin = InstalledPluginRecord(manifest=manifest, path=str(tmp_path))
        dialog = PluginDetailsDialog(
            plugin,
            runtime_service=Mock(get_settings_widget=Mock(return_value=None)),
            state_service=Mock(),
            app_state=SimpleNamespace(local_config={}),
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            Mock(side_effect=RuntimeError("dialog already deleted")),
        )

        dialog._confirm_delete_plugin()

        assert dialog.delete_requested is False
        assert dialog.result() == QDialog.DialogCode.Rejected
        _close_dialog(qapp, dialog)


class TestModPriorityStepsDialog:
    """Tests for dialogs."""

    def test_reordering_rows_persists_and_cancel_restores_initial_order(self, qapp, app_state):
        from ui.dialogs.mod.priority_steps_dialog import ModPriorityStepsDialog

        changes = Mock()
        dialog = ModPriorityStepsDialog([["first", "second"]], app_state, on_change=changes)
        model = dialog._step_lists[0].model()
        changes.assert_not_called()

        assert model is not None
        assert model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 2)

        changes.assert_called_once_with([["second", "first"]])
        assert dialog.get_result() == [["second", "first"]]
        dialog.reject()
        assert changes.call_args.args == ([["first", "second"]],)
        _close_dialog(qapp, dialog)

    def test_mod_priority_steps_dialog_groups_mods(self, qapp, app_state):
        from models.mod_models import ModInfo
        from ui.dialogs.mod.priority_steps_dialog import ModPriorityStepsDialog
        mods = [
            ModInfo(id=f"test_mod_{index}", name=f"Test Mod {index}", version='1.0.0', authors=['Author'], description='', game_version='', description_url='', downloads=0, game='deltarune')
            for index in (1, 2)
        ]
        dialog = ModPriorityStepsDialog([mods], app_state, None)

        dialog._add_step()
        dialog._move_mod_to_step(mods[1], 1)

        assert dialog.get_result() == [[mods[0]], [mods[1]]]
        assert dialog._active_step_index == 1
        _close_dialog(qapp, dialog)

    def test_mod_movement_uses_actively_interacted_step(self, qapp, app_state):
        from models.mod_models import ModInfo
        from ui.dialogs.mod.priority_steps_dialog import ModPriorityStepsDialog

        mods = [
            ModInfo(id=str(index), name=str(index), version="1", authors=[], description="", game_version="", description_url="", downloads=0, game="deltarune")
            for index in range(4)
        ]
        dialog = ModPriorityStepsDialog(
            [[mods[0], mods[1]], [mods[2], mods[3]]], app_state
        )
        dialog._step_lists[0].setCurrentRow(0)
        dialog._step_lists[1].setCurrentRow(1)
        dialog._set_active_step(1)

        dialog._move_selected_mod(-1)

        assert dialog.get_result() == [[mods[0], mods[1]], [mods[3], mods[2]]]
        _close_dialog(qapp, dialog)

    def test_selected_step_can_be_moved_and_removed(self, qapp, app_state):
        from models.mod_models import ModInfo
        from ui.dialogs.mod.priority_steps_dialog import ModPriorityStepsDialog

        mods = [
            ModInfo(id=str(index), name=str(index), version="1", authors=[], description="", game_version="", description_url="", downloads=0, game="deltarune")
            for index in range(3)
        ]
        dialog = ModPriorityStepsDialog([[mods[0]], [mods[1]], [mods[2]]], app_state)

        dialog._set_active_step(1)
        dialog._move_step(-1)
        assert dialog.get_result() == [[mods[1]], [mods[0]], [mods[2]]]
        assert dialog._active_step_index == 0

        dialog._set_active_step(1)
        dialog._remove_selected_step()
        assert dialog.get_result() == [[mods[1], mods[0]], [mods[2]]]
        assert dialog._active_step_index == 0
        _close_dialog(qapp, dialog)

    def test_mod_selection_is_exclusive_across_steps(self, qapp, app_state):
        from models.mod_models import ModInfo
        from ui.dialogs.mod.priority_steps_dialog import ModPriorityStepsDialog

        mods = [
            ModInfo(id=str(index), name=str(index), version="1", authors=[], description="", game_version="", description_url="", downloads=0, game="deltarune")
            for index in range(2)
        ]
        dialog = ModPriorityStepsDialog([[mods[0]], [mods[1]]], app_state)

        dialog._step_lists[0].setCurrentRow(0)
        assert dialog._step_lists[0].selectedItems()
        dialog._step_lists[1].setCurrentRow(0)

        assert not dialog._step_lists[0].selectedItems()
        assert dialog._step_lists[0].currentItem() is None
        assert dialog._step_lists[1].selectedItems()
        assert dialog._active_step_index == 1
        _close_dialog(qapp, dialog)

    def test_step_selection_is_visible_and_dialog_is_at_least_550_pixels_wide(
        self, qapp, app_state
    ):
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtWidgets import QApplication

        from ui.dialogs.mod.priority_steps_dialog import ModPriorityStepsDialog

        dialog = ModPriorityStepsDialog([[]], app_state)
        dialog._add_step()
        dialog.show()
        qapp.processEvents()

        click_position = QPointF(8, 8)
        mouse_press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            click_position,
            QPointF(dialog._step_groups[1].mapToGlobal(click_position.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(dialog._step_groups[1], mouse_press)

        assert dialog.minimumWidth() == 550
        assert dialog.width() >= 550
        assert dialog._step_groups[0].property("activeStep") is False
        assert dialog._step_groups[1].property("activeStep") is True
        _close_dialog(qapp, dialog)

    def test_priority_steps_dialog_refreshes_theme_and_localization(
        self, qapp, app_state, monkeypatch
    ):
        from ui.common.dialog_theme import get_dialog_theme_values
        from ui.dialogs.mod import priority_steps_dialog as module

        dialog = module.ModPriorityStepsDialog([[]], app_state)
        dialog._add_step()
        dialog.apply_theme()

        assert dialog.styleSheet().count('QGroupBox[activeStep="true"]') == 1
        assert "dashed" in dialog.styleSheet()
        assert get_dialog_theme_values(app_state)["select"] in dialog.styleSheet()

        monkeypatch.setattr(
            module,
            "tr",
            lambda key, **kwargs: f"translated:{key}:{kwargs.get('number', '')}",
        )
        dialog.relocalize_ui()

        assert dialog.windowTitle() == "translated:ui.priority_steps_title:"
        assert dialog._step_groups[1].title() == "translated:ui.step_number:2"
        assert dialog._localized_buttons["ui.add_step"].text() == "translated:ui.add_step:"
        _close_dialog(qapp, dialog)


class TestAboutDialog:
    """Tests for dialogs."""
    def test_about_dialog_creation(self, qapp, app_state, temp_dir):
        """Checks that abouting dialog creation."""
        from models.plugin_models import PLUGIN_API_VERSION
        from ui.dialogs.about_dialog import AboutDialog
        dialog = AboutDialog(None, app_state)
        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert dialog.title_label.text() == 'G3M'
        assert dialog.data_path_edit.text() == temp_dir
        assert dialog.plugin_api_value.text() == PLUGIN_API_VERSION
        assert dialog.os_value.text()
        assert dialog.python_value.text()
        _close_dialog(qapp, dialog)

    def test_about_dialog_actions(self, qapp, app_state):
        """Checks that abouting dialog actions."""
        from ui.dialogs.about_dialog import AboutDialog
        dialog = AboutDialog(None, app_state)
        with patch('ui.dialogs.about_dialog.open_url_native') as open_url, patch(
            'ui.dialogs.about_dialog.open_path_native'
        ) as open_path:
            dialog.wiki_button.click()
            dialog.open_folder_button.click()
            open_url.assert_called_once()
            open_path.assert_called_once()
        assert dialog.result() == QDialog.DialogCode.Rejected


class TestChangelogDialog:
    """Tests for dialogs."""
    def test_changelog_dialog_creation_without_source(self, qapp):
        """Checks that changeloging dialog creation without source."""
        from ui.dialogs.changelog_dialog import ChangelogDialog
        dialog = ChangelogDialog(None, '')
        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert hasattr(dialog, 'text_browser')
        assert hasattr(dialog, 'close_button')
        _close_dialog(qapp, dialog)


class TestLogViewerDialog:
    """Tests for dialogs."""

    def test_log_viewer_dialog_creation_and_relocalize(self, qapp, app_state, tmp_path):
        from services.localization_service import tr
        from ui.dialogs.log_viewer_dialog import LogViewerDialog

        logs_dir = tmp_path / "logs"
        patching_dir = logs_dir / "patching"
        patching_dir.mkdir(parents=True)
        (logs_dir / "g3m.log").write_text("g3m line\n", encoding="utf-8")
        (logs_dir / "patching.log").write_text("patching line\n", encoding="utf-8")
        (logs_dir / "conflicts.log").write_text("conflict line\n", encoding="utf-8")

        dialog = LogViewerDialog(app_state, parent=None, user_data_root=str(tmp_path))

        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert dialog._tabs.count() == 3
        assert dialog._tabs.tabText(0) == "G3M"
        assert dialog._tabs.tabText(1) == "Patching"
        assert dialog._tabs.tabText(2) == "Conflicts"
        assert dialog._history_combo.itemText(0) == tr("log_viewer.latest_live")
        assert dialog._open_folder_button.toolTip() == tr("log_viewer.open_folder")
        assert dialog._viewer.toPlainText() == "g3m line\n"

        dialog.relocalize_ui()
        dialog.refresh_theme()

        assert dialog.windowTitle() == tr("log_viewer.title")
        assert dialog._close_button.text() == tr("common.close")
        _close_dialog(qapp, dialog)

    def test_log_viewer_dialog_shows_blank_for_existing_empty_file(
        self, qapp, app_state, tmp_path
    ):
        from ui.dialogs.log_viewer_dialog import LogViewerDialog

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "patching.log").write_text("", encoding="utf-8")

        dialog = LogViewerDialog(app_state, parent=None, user_data_root=str(tmp_path))
        dialog._tabs.setCurrentIndex(1)
        qapp.processEvents()

        assert dialog._viewer.toPlainText() == ""
        _close_dialog(qapp, dialog)


class TestPizzaOvenConversionDialog:
    """Tests for dialogs."""
    def test_dialog_creation(self, qapp):
        """Checks that dialoging creation."""
        from services.localization_service import tr
        from ui.dialogs.pizza_oven_conversion_dialog import PizzaOvenConversionDialog

        dialog = PizzaOvenConversionDialog(None)

        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert dialog.windowTitle() == tr("dialogs.po_convert_title")
        assert dialog.start_button is not None
        assert dialog.start_button.text() == tr("buttons.start_po_convert")
        assert dialog.cancel_button is not None
        assert dialog.cancel_button.text() == tr("dialogs.cancel")
        _close_dialog(qapp, dialog)


class TestGameManagerDialog:
    def test_toggle_visibility_does_not_crash_if_warning_dialog_fails(
        self, qapp, app_state, monkeypatch
    ):
        """Checks that validation errors survive fallback warning dialog failures."""
        from PyQt6.QtWidgets import QMessageBox

        from services.game_registry_service import GameRegistryValidationError
        from ui.dialogs.game.manager_dialog import GameManagerDialog

        registry_service = Mock()
        registry_service.games_changed.connect = Mock()
        registry_service.list_manager_games.return_value = [
            SimpleNamespace(
                id="deltarune",
                display_name="DELTARUNE",
                is_builtin=True,
                is_visible=True,
                steam_app_id=None,
                gamebanana_id=None,
            )
        ]
        registry_service.set_visibility.side_effect = GameRegistryValidationError(
            "games.error_last_visible"
        )

        def fail_warning(*_args, **_kwargs):
            raise RuntimeError("dialog failed")

        monkeypatch.setattr(QMessageBox, "warning", fail_warning)

        dialog = GameManagerDialog(
            registry_service,
            profile_service=Mock(),
            game_versions_manager=Mock(),
            settings_service=Mock(),
            app_state=app_state,
        )
        dialog._on_toggle_visibility("deltarune", False)
        _close_dialog(qapp, dialog)


class TestBlocklistDialog:
    def test_empty_value_does_not_crash_if_warning_dialog_fails(
        self, qapp, monkeypatch
    ):
        """Checks that empty blocklist validation survives warning dialog failures."""
        from PyQt6.QtWidgets import QMessageBox

        from ui.dialogs.blocklist_dialog import BlocklistDialog

        service = Mock()
        service.get_prefix_types.return_value = [("name", "Name")]
        service.get_blocklist_for_game.return_value = []
        service.get_prefix_type_display_name.return_value = "Name"

        def fail_warning(*_args, **_kwargs):
            raise RuntimeError("dialog failed")

        monkeypatch.setattr(QMessageBox, "warning", fail_warning)

        dialog = BlocklistDialog(
            service,
            current_game="deltarune",
            available_games=[_GameEntry(id="deltarune", display_name="DELTARUNE")],
        )
        dialog.value_edit.setText("")
        dialog.add_entry()
        _close_dialog(qapp, dialog)


class TestReadmeUi:
    """Tests for dialogs."""

    def test_empty_info_dialog_centers_info_only_message(self, qapp, app_state):
        from services.localization_service import localization_service, tr
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        dialog = ModReadmeDialog(app_state, "Test Mod", [])
        dialog.resize(920, 680)
        dialog.show()
        qapp.processEvents()

        assert dialog._content_stack.currentWidget() is dialog._empty_page
        assert "README" not in dialog.windowTitle()
        assert "README" not in dialog._empty_label.text()
        assert abs(
            dialog._empty_label.geometry().center().y()
            - dialog._empty_page.rect().center().y()
        ) <= 1
        _close_dialog(qapp, dialog)

        original_language = localization_service.get_current_language()
        try:
            for language in localization_service.get_available_languages():
                assert localization_service.load_language(language)
                assert "README" not in tr(
                    "dialogs.readme_viewer_title", mod_name="Test Mod"
                )
                assert "README" not in tr("dialogs.no_readme_files")
        finally:
            localization_service.load_language(original_language)

    def test_mod_readme_dialog_creation(self, qapp, app_state, tmp_path):
        """Checks that mod readme dialog creation."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.md"
        readme_path.write_text("# Guide\n\n[Link](https://example.com)", encoding="utf-8")

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])

        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert dialog._tabs.count() == 1
        assert "QTabWidget::pane" in dialog.styleSheet()
        assert "padding-top: 10px;" in dialog.styleSheet()
        _close_dialog(qapp, dialog)

    def test_mod_readme_dialog_loads_a_listed_archive_member(
        self, qapp, app_state, tmp_path
    ):
        """Materializes an archive member only while its tab is open."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        archive = tmp_path / "docs.zip"
        with ZipFile(archive, "w") as writer:
            writer.writestr("Guide.md", "# Archive guide")
        dialog = ModReadmeDialog(app_state, "Test Mod", [f"{archive}/Guide.md"])
        tab = dialog._tabs.widget(0)

        assert "Archive guide" in vars(tab)["viewer"].toPlainText()
        assert vars(tab)["_temporary_directory"] is not None
        _close_dialog(qapp, dialog)
        assert vars(tab)["_temporary_directory"] is None

    def test_mod_readme_dialog_shows_unlisted_files_only_on_request(
        self, qapp, app_state, tmp_path
    ):
        """Keeps unlisted readable files out of the initial tab set."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        listed = tmp_path / "listed.md"
        unlisted = tmp_path / "unlisted.md"
        listed.write_text("Listed", encoding="utf-8")
        unlisted.write_text("Unlisted", encoding="utf-8")

        dialog = ModReadmeDialog(
            app_state, "Test Mod", [str(listed)], [str(unlisted)]
        )

        assert dialog._tabs.count() == 1
        assert not dialog._unlisted_checkbox.isHidden()
        dialog._unlisted_checkbox.setChecked(True)
        qapp.processEvents()
        assert dialog._tabs.count() == 2
        _close_dialog(qapp, dialog)

    def test_mod_readme_markdown_heading_keeps_inline_format_size(
        self, qapp, app_state, tmp_path
    ):
        """Checks that heading inline markup keeps the heading font size."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.md"
        readme_path.write_text(
            "### Start **Bold** _Emphasis_ [Link](https://example.com) Tail",
            encoding="utf-8",
        )

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)
        block = vars(tab)["viewer"].document().begin()
        sizes = []
        anchors = []
        cursor = QTextCursor(block)
        for _ in range(block.length() - 1):
            cursor.movePosition(
                QTextCursor.MoveOperation.NextCharacter,
                QTextCursor.MoveMode.KeepAnchor,
            )
            text = cursor.selectedText()
            if text.strip():
                fmt = cursor.charFormat()
                sizes.append(round(fmt.font().pointSizeF(), 2))
                anchors.append(fmt.anchorHref())
            cursor.clearSelection()

        assert len(set(sizes)) == 1
        assert any(anchor == "https://example.com" for anchor in anchors)
        _close_dialog(qapp, dialog)

    def test_mod_readme_markdown_accepts_indented_heading_marks(
        self, qapp, app_state, tmp_path
    ):
        """Checks that copied docs with indented heading marks still render headings."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.md"
        readme_path.write_text(
            "  ### Sigma\n\n\t### another sigma\n\n\u00a0### third sigma",
            encoding="utf-8",
        )

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)

        assert vars(tab)["viewer"].toPlainText() == "Sigma\nanother sigma\nthird sigma"
        _close_dialog(qapp, dialog)

    def test_mod_readme_markdown_accepts_escaped_heading_marks(
        self, qapp, app_state, tmp_path
    ):
        """Checks that editor-escaped heading marks still render headings."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.md"
        readme_path.write_text("\\### Sigma\n\n\\### another sigma", encoding="utf-8")

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)

        assert vars(tab)["viewer"].toPlainText() == "Sigma\nanother sigma"
        _close_dialog(qapp, dialog)

    def test_mod_readme_markdown_preserves_heading_levels_and_fenced_code(
        self, qapp, app_state, tmp_path
    ):
        """Checks that all heading levels render while fenced code stays literal."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.md"
        readme_path.write_text(
            "\n".join(
                [
                    "# H1",
                    "## H2",
                    "### H3",
                    "#### H4",
                    "##### H5",
                    "###### H6",
                    "```",
                    "\\### not a heading",
                    "```",
                ]
            ),
            encoding="utf-8",
        )

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)
        levels = []
        block = vars(tab)["viewer"].document().begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if level:
                levels.append(level)
            block = block.next()

        assert levels == [1, 2, 3, 4, 5, 6]
        assert "\\### not a heading" in vars(tab)["viewer"].toPlainText()
        _close_dialog(qapp, dialog)

    def test_mod_readme_markdown_renders_common_inline_formatting(
        self, qapp, app_state, tmp_path
    ):
        """Checks that common Markdown inline formatting survives rendering."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.md"
        readme_path.write_text(
            "**bold** *italic* _under_ [link](https://example.com) <u>htmlu</u>",
            encoding="utf-8",
        )

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)
        document = vars(tab)["viewer"].document()

        assert document.find("bold").charFormat().fontWeight() > 400
        assert document.find("italic").charFormat().fontItalic()
        assert document.find("under").charFormat().fontUnderline()
        assert document.find("link").charFormat().anchorHref() == "https://example.com"
        assert document.find("htmlu").charFormat().fontUnderline()
        _close_dialog(qapp, dialog)

    def test_mod_readme_html_renders_as_html(self, qapp, app_state, tmp_path):
        """Checks that HTML INFO files render instead of showing raw tags."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.html"
        readme_path.write_text("<h1>Guide</h1><p>Rendered <b>HTML</b></p>", encoding="utf-8")

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)
        cursor = vars(tab)["viewer"].document().find("HTML")

        assert vars(tab)["viewer"].toPlainText() == "Guide\nRendered HTML"
        assert cursor.charFormat().fontWeight() > 400
        _close_dialog(qapp, dialog)

    def test_mod_readme_html_loads_relative_local_image(self, qapp, app_state, tmp_path):
        """Checks that HTML INFO files can render images beside the mod file."""
        from PyQt6.QtGui import QImage

        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        image_dir = tmp_path / "images"
        image_dir.mkdir()
        image_path = image_dir / "debug.png"
        image = QImage(12, 12, QImage.Format.Format_RGB32)
        image.fill(0x00FF00)
        assert image.save(str(image_path))
        readme_path = tmp_path / "Debug Mode Controls.html"
        readme_path.write_text(
            '<h1>Debug Mode Controls</h1><img src="images/debug.png" width="80">',
            encoding="utf-8",
        )

        dialog = ModReadmeDialog(app_state, "Debug Mode Controls", [str(readme_path)])
        tab = dialog._tabs.widget(0)
        qapp.processEvents()

        resource_url = QUrl.fromLocalFile(str(image_path))
        resource = vars(tab)["viewer"].document().resource(
            QTextDocument.ResourceType.ImageResource,
            resource_url,
        )
        assert not resource.isNull()
        assert resource.width() == 12
        assert "Debug Mode Controls" in vars(tab)["viewer"].toPlainText()
        _close_dialog(qapp, dialog)

    def test_mod_readme_html_loads_remote_image_from_local_file(
        self, qapp, app_state, tmp_path
    ):
        """Checks that local HTML INFO files may load remote img sources."""
        from PyQt6.QtGui import QImage

        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(0x0000FF)
        url = "https://example.invalid/remote.png"
        readme_path = tmp_path / "Remote.html"
        readme_path.write_text(f'<h1>Remote</h1><img src="{url}" width="96">', encoding="utf-8")

        with patch("ui.common.rich_html.get_session") as get_session:
            response = Mock()
            image_bytes = bytearray()
            buffer = QImage(image)
            from PyQt6.QtCore import (
                QBuffer,
                QByteArray,
                QIODevice,
            )

            data = QByteArray()
            qbuffer = QBuffer(data)
            qbuffer.open(QIODevice.OpenModeFlag.WriteOnly)
            assert buffer.save(qbuffer, "PNG")
            image_bytes.extend(data.data())
            response.content = bytes(image_bytes)
            response.raise_for_status = Mock()
            get_session.return_value.get.return_value = response
            dialog = ModReadmeDialog(app_state, "Remote", [str(readme_path)])
            tab = dialog._tabs.widget(0)
            wait = cast(Callable[[int], None], QTest.qWait)
            resource = QImage()
            for _ in range(20):
                qapp.processEvents()
                wait(25)

                resource = vars(tab)["viewer"].document().resource(
                    QTextDocument.ResourceType.ImageResource,
                    QUrl(url),
                )
                if not resource.isNull() and resource.width() == 24:
                    break
            assert not resource.isNull()
            assert resource.width() == 24
            _close_dialog(qapp, dialog)

    def test_mod_readme_html_rerenders_images_after_resize(
        self, qapp, app_state, tmp_path
    ):
        """Checks that lazy HTML loading does not lock images to fallback width."""
        from PyQt6.QtGui import QImage

        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        image_dir = tmp_path / "images"
        image_dir.mkdir()
        image_path = image_dir / "wide.png"
        image = QImage(640, 120, QImage.Format.Format_RGB32)
        image.fill(0x00FF00)
        assert image.save(str(image_path))
        readme_path = tmp_path / "README.html"
        readme_path.write_text('<img src="images/wide.png" width="620">', encoding="utf-8")

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        dialog.resize(900, 640)
        dialog.show()
        wait = cast(Callable[[int], None], QTest.qWait)
        for _ in range(5):
            qapp.processEvents()
            wait(30)
        tab = dialog._tabs.widget(0)
        html = vars(tab)["viewer"].toHtml()

        assert str(image_path).replace("\\", "/") in html
        _close_dialog(qapp, dialog)

    def test_mod_readme_html_renders_common_formatting(
        self, qapp, app_state, tmp_path
    ):
        """Checks that common HTML formatting survives rendering."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.html"
        readme_path.write_text(
            "<h1>Title</h1><p><strong>bold</strong> <em>italic</em> "
            "<u>under</u> <a href='https://example.com'>link</a></p>",
            encoding="utf-8",
        )

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)
        document = vars(tab)["viewer"].document()

        assert document.find("Title").block().blockFormat().headingLevel() == 1
        assert document.find("bold").charFormat().fontWeight() > 400
        assert document.find("italic").charFormat().fontItalic()
        assert document.find("under").charFormat().fontUnderline()
        assert document.find("link").charFormat().anchorHref() == "https://example.com"
        _close_dialog(qapp, dialog)

    def test_mod_readme_html_stays_unstyled_next_to_markdown(
        self, qapp, app_state, tmp_path
    ):
        """Checks that HTML tabs keep their own default styling in mixed readme sets."""
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        md_path = tmp_path / "README.md"
        html_path = tmp_path / "README.html"
        md_path.write_text("# Guide", encoding="utf-8")
        html_path.write_text("<p><a href='https://example.com'>Link</a></p>", encoding="utf-8")

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(md_path), str(html_path)])
        dialog._tabs.setCurrentIndex(1)
        qapp.processEvents()
        tab = dialog._tabs.widget(1)

        assert vars(tab)["viewer"].document().defaultStyleSheet().strip() == ""
        _close_dialog(qapp, dialog)

    def test_mod_readme_pdf_loads_in_pdf_viewer(self, qapp, app_state, tmp_path):
        """Checks that PDF INFO files load through Qt PDF support."""
        from PyQt6.QtPdfWidgets import QPdfView

        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.pdf"
        readme_path.write_bytes(
            b"%PDF-1.1\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
            b"4 0 obj\n<< /Length 44 >>\nstream\n"
            b"BT /F1 12 Tf 72 120 Td (Hello PDF) Tj ET\n"
            b"endstream\nendobj\n"
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
            b"xref\n0 6\n0000000000 65535 f \n"
            b"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n405\n%%EOF\n"
        )

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)

        assert isinstance(vars(tab)["pdf_viewer"], QPdfView)
        assert vars(tab)["_pdf_document"].pageCount() == 1
        _close_dialog(qapp, dialog)

    def test_mod_readme_pdf_error_shows_loading_error(self, qapp, app_state, tmp_path):
        """Checks that unreadable PDF INFO files show an error state."""
        from services.localization_service import tr
        from ui.dialogs.mod.readme_dialog import ModReadmeDialog

        readme_path = tmp_path / "README.pdf"
        readme_path.write_bytes(b"not a pdf")

        dialog = ModReadmeDialog(app_state, "Test Mod", [str(readme_path)])
        tab = dialog._tabs.widget(0)

        assert vars(tab)["pdf_viewer"].isHidden()
        assert not vars(tab)["pdf_error_label"].isHidden()
        assert vars(tab)["pdf_error_label"].text() == tr("status.loading_error")
        _close_dialog(qapp, dialog)

    def test_mod_summary_panel_uses_localized_info_button(self, qapp, app_state):
        """Checks that mod summary panel uses localized info button."""
        from services.localization_service import tr
        from ui.widgets.mod.mod_summary_panel import ModSummaryPanel

        panel = ModSummaryPanel(app_state)

        assert panel._readme_button.text() == tr("dialogs.info")
        panel.update_labels_text()
        assert panel._readme_button.text() == tr("dialogs.info")
        panel.apply_theme()
        assert panel.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert panel._scroll.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert cast(QWidget, panel._scroll.viewport()).testAttribute(
            Qt.WidgetAttribute.WA_StyledBackground
        )
        panel.deleteLater()

    def test_profile_manager_metadata_uses_new_format(self, qapp, app_state):
        """Checks that profile manager metadata uses new format."""
        from unittest.mock import Mock

        from PyQt6.QtWidgets import QLabel

        from ui.dialogs.profile_manager_dialog import ProfileManagerDialog

        profile_service = Mock()
        profile_service.active_name = "Default"
        profile_service.list_profiles.return_value = ["Default"]
        profile_service.get_profile_summary.return_value = {
            "name": "Default",
            "game": "deltarune",
            "game_display_name": "DELTARUNE",
            "game_mod_count": 3,
            "total_mod_count": 7,
            "chapter_mode": False,
            "direct_launch": "",
        }

        dialog = ProfileManagerDialog(profile_service, app_state)
        item_widget = dialog.list_widget.itemWidget(dialog.list_widget.item(0))
        assert item_widget is not None
        detail_label = item_widget.findChild(QLabel, "profileDetailLabel")

        assert "3 mods for DELTARUNE" in detail_label.text()
        assert "7 mods in profile" in detail_label.text()
        dialog.close()

    def test_profile_manager_external_drop_imports_multiple_archives(self, qapp, app_state, temp_dir):
        """Checks that profile manager external drop imports multiple archives."""
        import os

        from ui.dialogs.profile_manager_dialog import ProfileManagerDialog

        first = os.path.join(temp_dir, "one.zip")
        second = os.path.join(temp_dir, "two.zip")
        open(first, "wb").close()
        open(second, "wb").close()

        profile_service = Mock()
        profile_service.active_name = "Default"
        profile_service.list_profiles.return_value = ["Default"]
        profile_service.get_profile_summary.return_value = {
            "name": "Default",
            "game": "deltarune",
            "game_display_name": "DELTARUNE",
            "game_mod_count": 1,
            "total_mod_count": 1,
            "chapter_mode": False,
            "direct_launch": "",
        }
        profile_service.import_profile.side_effect = ["ImportedOne", "ImportedTwo"]

        dialog = ProfileManagerDialog(profile_service, app_state)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(first), QUrl.fromLocalFile(second)])

        event = SimpleNamespace(accepted=False)
        event.mimeData = lambda: mime
        event.source = lambda: None
        event.acceptProposedAction = lambda: vars(event).__setitem__("accepted", True)
        with patch("ui.dialogs.profile_manager_dialog.QMessageBox.information"):
            dialog.list_widget.dropEvent(cast(QDropEvent, event))
        assert event.accepted is True
        assert os.path.normpath(profile_service.import_profile.call_args_list[0].args[0]) == os.path.normpath(first)
        assert os.path.normpath(profile_service.import_profile.call_args_list[1].args[0]) == os.path.normpath(second)
        dialog.close()

    def test_profile_manager_import_error_does_not_crash_if_critical_dialog_fails(
        self, qapp, app_state, tmp_path, monkeypatch
    ):
        """Checks that profile import errors survive fallback critical dialog failures."""
        from PyQt6.QtWidgets import QMessageBox

        from ui.dialogs.profile_manager_dialog import ProfileManagerDialog

        archive = tmp_path / "broken.zip"
        archive.write_bytes(b"not a profile")

        profile_service = Mock()
        profile_service.active_name = "Default"
        profile_service.list_profiles.return_value = ["Default"]
        profile_service.get_profile_summary.return_value = {
            "name": "Default",
            "game": "deltarune",
            "game_display_name": "DELTARUNE",
            "game_mod_count": 1,
            "total_mod_count": 1,
            "chapter_mode": False,
            "direct_launch": "",
        }
        profile_service.import_profile.side_effect = RuntimeError("profile broken")

        def fail_critical(*_args, **_kwargs):
            raise RuntimeError("dialog failed")

        monkeypatch.setattr(QMessageBox, "critical", fail_critical)

        dialog = ProfileManagerDialog(profile_service, app_state)
        dialog.import_profiles_from_paths([str(archive)])
        _close_dialog(qapp, dialog)

    def test_game_versions_dialog_drop_imports_multiple_files_and_urls(self, qapp, app_state, temp_dir):
        """Checks that game versions dialog drop imports multiple files and urls."""
        import os

        from ui.dialogs.game.versions_dialog import GameVersionsDialog

        first = os.path.join(temp_dir, "one.zip")
        second = os.path.join(temp_dir, "two.zip")
        open(first, "wb").close()
        open(second, "wb").close()

        manager = Mock()
        manager.records_for_game.return_value = []
        manager.record_added.connect = Mock()
        manager.record_removed.connect = Mock()
        manager.record_updated.connect = Mock()
        manager.progress_updated.connect = Mock()
        manager.operation_error.connect = Mock()

        dialog = GameVersionsDialog(manager, app_state)
        mime = QMimeData()
        mime.setUrls(
            [
                QUrl.fromLocalFile(first),
                QUrl.fromLocalFile(second),
                QUrl("https://example.com/one.zip"),
                QUrl("https://example.com/two.zip"),
            ]
        )

        event = SimpleNamespace(accepted=False)
        event.mimeData = lambda: mime
        event.source = lambda: None
        event.acceptProposedAction = lambda: vars(event).__setitem__("accepted", True)
        event.ignore = lambda: vars(event).__setitem__("accepted", False)
        dialog.dropEvent(cast(QDropEvent, event))
        game_id = dialog._current_game()
        assert event.accepted is True
        actual_first_path = manager.import_game_version_from_file.call_args_list[0].args[1]
        actual_second_path = manager.import_game_version_from_file.call_args_list[1].args[1]
        assert os.path.normpath(actual_first_path) == os.path.normpath(first)
        assert os.path.normpath(actual_second_path) == os.path.normpath(second)
        assert manager.import_game_version_from_url.call_args_list[0].args == (game_id, "https://example.com/one.zip")
        assert manager.import_game_version_from_url.call_args_list[1].args == (game_id, "https://example.com/two.zip")
        dialog.close()

    def test_game_versions_error_does_not_crash_if_warning_dialog_fails(
        self, qapp, app_state, monkeypatch
    ):
        """Checks that operation errors survive fallback warning dialog failures."""
        from PyQt6.QtWidgets import QMessageBox

        from ui.dialogs.game.versions_dialog import GameVersionsDialog

        manager = Mock()
        manager.records_for_game.return_value = []
        manager.record_added.connect = Mock()
        manager.record_removed.connect = Mock()
        manager.record_updated.connect = Mock()
        manager.progress_updated.connect = Mock()
        manager.operation_error.connect = Mock()

        def fail_warning(*_args, **_kwargs):
            raise RuntimeError("dialog failed")

        monkeypatch.setattr(QMessageBox, "warning", fail_warning)

        dialog = GameVersionsDialog(manager, app_state)
        dialog._on_error("operation failed")
        dialog.close()

    def test_mod_versions_dialog_drop_queues_multiple_imports(self, qapp, app_state, tmp_path):
        """Checks that mod versions dialog drop queues multiple imports."""
        import os

        from ui.dialogs.mod.versions_dialog import ModVersionsDialog

        mod_folder = tmp_path / "mod"
        mod_folder.mkdir()
        (mod_folder / "mod_config.json").write_text('{"name":"Mod"}', encoding="utf-8")
        first = tmp_path / "one.zip"
        second = tmp_path / "two.zip"
        first.write_bytes(b"1")
        second.write_bytes(b"2")

        dialog = ModVersionsDialog(
            str(mod_folder),
            {"id": "mod", "name": "Mod"},
            app_state,
            parent=None,
        )
        imported_files = []
        imported_urls = []
        vars(dialog)["_import_from_path"] = lambda path, version_name=None, prompt_for_name=True: imported_files.append((path, prompt_for_name)) or dialog._process_next_import()
        dialog._start_url_worker = lambda url, version_name=None, prompt_for_name=True: imported_urls.append((url, prompt_for_name)) or dialog._process_next_import()

        mime = QMimeData()
        mime.setUrls(
            [
                QUrl.fromLocalFile(str(first)),
                QUrl.fromLocalFile(str(second)),
                QUrl("https://example.com/modA.zip"),
                QUrl("https://example.com/modB.zip"),
            ]
        )

        event = SimpleNamespace(accepted=False)
        event.mimeData = lambda: mime
        event.source = lambda: None
        event.acceptProposedAction = lambda: vars(event).__setitem__("accepted", True)
        event.ignore = lambda: vars(event).__setitem__("accepted", False)
        dialog.dropEvent(cast(QDropEvent, event))
        assert event.accepted is True
        assert [(os.path.normpath(path), flag) for path, flag in imported_files] == [(os.path.normpath(str(first)), False), (os.path.normpath(str(second)), False)]
        assert imported_urls == [
            ("https://example.com/modA.zip", False),
            ("https://example.com/modB.zip", False),
        ]
        dialog.close()


class TestThemeManagementDialog:
    """Tests for dialogs."""

    def test_theme_import_dialog_uses_real_localized_text(self, qapp):
        """Checks that theme import dialog never shows raw localization keys."""
        from unittest.mock import Mock

        from ui.dialogs.import_dialog import ImportDialog

        dialog = ImportDialog(None, Mock(), "themes", "*.zip")
        visible_text = [dialog.windowTitle(), dialog.url_input.placeholderText()]
        visible_text.extend(
            widget.text()
            for widget in dialog.findChildren((QLabel, QPushButton))
            if widget.text()
        )

        assert visible_text
        assert all("themes." not in text for text in visible_text)
        assert all(not (text.startswith("[") and text.endswith("]")) for text in visible_text)
        dialog.close()

    def test_theme_management_dialog_creation(self, qapp, app_state):
        """Checks that themeing management dialog creation."""
        from unittest.mock import Mock

        from services.customization_service import CustomizationManager
        from services.localization_service import tr
        from ui.dialogs.theme_dialog import ThemeManagementDialog

        class FakeThemeController:
            def __init__(self, state) -> None:
                self.app_state = state
                self.customization_service = CustomizationManager(state)
                self.settings_service = Mock()

        theme_controller = FakeThemeController(app_state)
        dialog = ThemeManagementDialog(None, theme_controller)
        assert dialog is not None
        assert isinstance(dialog, QDialog)
        assert dialog.findChildren(QPushButton)[0].toolTip() == tr("tooltips.import_theme")

        settings_text = dialog._build_settings_text()
        assert isinstance(settings_text, str)
        assert 'themes.no_customizations' in settings_text or len(settings_text) > 0
        dialog.close()

    def test_theme_management_dialog_hides_default_border_radius(
        self, qapp, app_state
    ):
        """Checks that themeing management dialog hides default border radius."""
        from unittest.mock import Mock

        from services.customization_service import CustomizationManager
        from ui.dialogs.theme_dialog import ThemeManagementDialog

        class FakeThemeController:
            def __init__(self, state) -> None:
                self.app_state = state
                self.customization_service = CustomizationManager(state)
                self.settings_service = Mock()

        app_state.local_config["custom_border_radius"] = 7
        theme_controller = FakeThemeController(app_state)
        dialog = ThemeManagementDialog(None, theme_controller)

        settings_text = dialog._build_settings_text()

        assert "Border Radius" not in settings_text
        dialog.close()


class TestModdingToolsDialog:
    """Tests for dialogs."""
    def test_dialog_uses_raised_tab_styling(self, qapp, app_state):
        """Checks that dialoging uses raised tab styling."""
        from ui.dialogs.modding_tools_dialog import ModdingToolsDialog

        dialog = ModdingToolsDialog(Mock(), app_state)

        assert dialog._tabs.documentMode() is True
        assert "QTabWidget::pane" in dialog.styleSheet()
        assert "padding-top: 10px;" in dialog.styleSheet()
        dialog.close()


class TestDialogTheme:
    """Tests for dialogs."""
    def test_dialog_theme_uses_hover_and_checkbox_selection_colors(self, app_state):
        """List selection and checkbox state use their matching theme colors."""
        from ui.common.dialog_theme import build_dialog_theme_stylesheet

        app_state.local_config = {
            'custom_hover_color': '#112233',
            'custom_select_color': '#445566',
        }
        stylesheet = build_dialog_theme_stylesheet(app_state)

        assert 'background-color: #112233;' in stylesheet
        assert 'selection-background-color: #112233;' in stylesheet
        assert 'QTreeWidget::indicator:checked' in stylesheet
        assert '#445566' in stylesheet


class TestManualInstallDialog:
    """Tests for dialogs."""
    def test_profile_manager_import_failure_uses_localized_filesystem_error(
        self, qapp, tmp_path, monkeypatch
    ):
        from services.localization_service import tr
        from ui.dialogs.profile_manager_dialog import ProfileManagerDialog

        archive_path = tmp_path / "profile.zip"
        archive_path.write_bytes(b"zip")
        profile_service = Mock()
        profile_service.active_name = "Default"
        profile_service.list_profiles.return_value = ["Default"]
        profile_service.get_profile_summary.return_value = {
            "name": "Default",
            "game": "deltarune",
            "game_display_name": "DELTARUNE",
            "game_mod_count": 0,
            "total_mod_count": 0,
            "chapter_mode": False,
            "direct_launch": "",
        }
        profile_service.import_profile.side_effect = PermissionError(
            13, "Permission denied", str(archive_path)
        )
        app_state = Mock(local_config={})
        dialog = ProfileManagerDialog(profile_service, app_state)
        calls = []
        monkeypatch.setattr(
            "ui.dialogs.profile_manager_dialog.QMessageBox.critical",
            lambda *args: calls.append(args),
        )

        dialog.import_profiles_from_paths([str(archive_path)])

        assert calls
        assert calls[0][2] == tr(
            "profiles.import_failed",
            error=tr("errors.permission_denied", path=str(archive_path)),
        )
        _close_dialog(qapp, dialog)
