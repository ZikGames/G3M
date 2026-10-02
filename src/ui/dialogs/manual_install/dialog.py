"""Create one current-format local mod from otherwise unrecognised files."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import override

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from config.config import MOD_DOCUMENTATION_EXTENSIONS
from models.game_modes import get_visible_game_entries
from services.localization_service import tr
from utils.file_utils import get_unique_mod_dir, remove_archive_extension
from utils.mod.config import MOD_CONFIG_TAGS, MOD_CONFIG_VERSION, write_mod_config
from utils.process_utils import format_filesystem_error

logger = logging.getLogger(__name__)


class ManualModInstallDialog(QDialog):
    """Copy source files first, then configure their explicit operations."""

    def __init__(
        self,
        parent,
        prepared_files_path: str,
        gamebanana_metadata: dict | None = None,
        source_file_path: str | None = None,
        initial_game_type: str | None = None,
        *, target_mods_dir: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.prepared_files_path = prepared_files_path
        self.gamebanana_metadata = gamebanana_metadata or {}
        self.source_file_path = source_file_path
        self.initial_game_type = initial_game_type
        self.target_mods_dir = target_mods_dir
        self.temp_dir_to_cleanup: str | None = None
        self.app_state, self.mod_service = self._find_services(parent)
        self.all_files = self._scan_files()
        self.resize(760, 560)
        self.setMinimumSize(620, 440)
        self.setModal(True)
        self._build_ui()
        self._populate()
        self.relocalize_ui()

    @staticmethod
    def _find_services(parent) -> tuple[object | None, object | None]:
        current, visited = parent, set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            state = getattr(current, "app_state", None)
            if state is not None:
                return state, getattr(current, "mod_service", None)
            getter = getattr(current, "parent", None)
            current = getter() if callable(getter) else None
        return None, None

    def _scan_files(self) -> list[tuple[str, str]]:
        root = Path(self.prepared_files_path)
        if not root.is_dir():
            return []
        return sorted(
            (
                (str(path), path.relative_to(root).as_posix())
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda item: item[1].casefold(),
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        form = QFormLayout()
        self.game_combo = QComboBox(self)
        for game in get_visible_game_entries():
            self.game_combo.addItem(game.display_name, game.id)
        form.addRow(QLabel(tr("ui.mod_type_label"), self), self.game_combo)
        self.name_edit = QLineEdit(self)
        form.addRow(QLabel(tr("ui.mod_name_label"), self), self.name_edit)
        self.authors_edit = QLineEdit(self)
        form.addRow(QLabel(tr("ui.mod_authors"), self), self.authors_edit)
        root.addLayout(form)
        self.files_summary_label = QLabel(self)
        self.files_summary_label.setObjectName("manualInstallHint")
        self.files_summary_label.setWordWrap(True)
        root.addWidget(self.files_summary_label)
        self.sources = QTreeWidget(self)
        self.sources.setHeaderHidden(True)
        root.addWidget(self.sources, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self.buttons.accepted.connect(self._on_finish)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _populate(self) -> None:
        source_name = (
            remove_archive_extension(os.path.basename(self.source_file_path))
            if self.source_file_path
            else Path(self.prepared_files_path).name
        )
        self.name_edit.setText(str(self.gamebanana_metadata.get("name") or source_name or "Manual Mod"))
        authors = self.gamebanana_metadata.get("authors")
        self.authors_edit.setText(
            ", ".join(str(name).strip() for name in authors if str(name).strip())
            if isinstance(authors, list)
            else "Unknown"
        )
        wanted = self.initial_game_type or ""
        for index in range(self.game_combo.count()):
            if self.game_combo.itemData(index) == wanted:
                self.game_combo.setCurrentIndex(index)
                break
        for _source, relative in self.all_files:
            self.sources.addTopLevelItem(QTreeWidgetItem([relative]))
        self.files_summary_label.setText(
            tr("ui.manual_install_hint", count=len(self.all_files))
        )

    def relocalize_ui(self) -> None:
        self.setWindowTitle(tr("dialogs.manual_install_title"))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr("ui.manual_install_configure")
        )

    @staticmethod
    def _safe_relative_path(value: str) -> PurePosixPath:
        relative = PurePosixPath(value.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("source path must remain inside the selected import")
        return relative

    def _identity(self) -> tuple[str, str]:
        metadata = self.gamebanana_metadata
        if metadata.get("mod_id"):
            item_type = str(metadata.get("item_type") or "mod").lower()
            mod_id = f"gb_{item_type}_{metadata['mod_id']}"
        else:
            mod_id = f"local_manual_{uuid.uuid4().hex[:12]}"
        return mod_id, self.name_edit.text().strip() or "Manual Mod"

    def _copy_sources(self, destination: Path) -> tuple[list[str], list[str]]:
        copied, docs = [], []
        for source, raw_relative in self.all_files:
            relative = self._safe_relative_path(raw_relative)
            stored = PurePosixPath("files") / relative
            target = destination.joinpath(*stored.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            stored_path = stored.as_posix()
            copied.append(stored_path)
            if target.suffix.lower() in MOD_DOCUMENTATION_EXTENSIONS:
                docs.append(stored_path)
        return copied, docs

    def _config(self, mod_id: str, mod_name: str, docs: list[str]) -> dict[str, object]:
        config: dict[str, object] = {
            "config_version": MOD_CONFIG_VERSION,
            "id": mod_id,
            "name": mod_name,
            "version": str(self.gamebanana_metadata.get("version") or "1.0.0"),
            "authors": [
                name.strip()
                for name in self.authors_edit.text().split(",")
                if name.strip()
            ]
            or ["Unknown"],
            "game": str(self.game_combo.currentData() or "deltarune"),
            "files": [
                {"source": f"${{mod_path}}/{path}", "type": "info"}
                for path in docs
            ],
        }
        for field in ("description", "homepage"):
            if value := self.gamebanana_metadata.get(field):
                config[field] = str(value)
        tags = self.gamebanana_metadata.get("tags")
        if isinstance(tags, list) and (valid := [tag for tag in tags if tag in MOD_CONFIG_TAGS]):
            config["tags"] = valid
        return config

    def _create_mod_from_files(self) -> tuple[dict[str, object], Path]:
        if self.app_state is None:
            raise RuntimeError("application state is unavailable")
        if not self.all_files:
            raise ValueError("no files were selected for import")
        mods_dir = Path(self.target_mods_dir or self.app_state.mods_dir)
        mods_dir.mkdir(parents=True, exist_ok=True)
        mod_id, mod_name = self._identity()
        target = mods_dir / get_unique_mod_dir(str(mods_dir), mod_name)
        target.mkdir(parents=True)
        try:
            _copied, docs = self._copy_sources(target)
            config = self._config(mod_id, mod_name, docs)
            write_mod_config(target / "mod_config.json", config)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return config, target

    def _open_editor(self, config: dict[str, object], folder: Path) -> bool:
        from ui.dialogs.mod_editor.dialog import ModEditorDialog

        payload = dict(config, folder_path=str(folder))
        return (
            ModEditorDialog(
                self.parentWidget() or self, is_creating=False, mod_data=payload
            ).exec()
            == QDialog.DialogCode.Accepted
        )

    def _on_finish(self) -> None:
        if not self.name_edit.text().strip():
            self._safe_warning(tr("errors.error"), tr("dialogs.mod_name_empty"))
            return
        try:
            config, folder = self._create_mod_from_files()
        except Exception as error:
            logger.exception("Manual import failed")
            self._safe_critical(
                tr("errors.error"),
                tr("errors.manual_install_failed", error=format_filesystem_error(error)),
            )
            return
        if self._open_editor(config, folder):
            self.accept()
            return
        shutil.rmtree(folder, ignore_errors=True)
        self.reject()

    @staticmethod
    def _safe_warning(title: str, message: str) -> None:
        try:
            QMessageBox.warning(None, title, message)
        except RuntimeError:
            logger.exception("Could not show manual-import warning")

    @staticmethod
    def _safe_critical(title: str, message: str) -> None:
        try:
            QMessageBox.critical(None, title, message)
        except RuntimeError:
            logger.exception("Could not show manual-import error")

    @override
    def closeEvent(self, event) -> None:
        if self.temp_dir_to_cleanup:
            shutil.rmtree(self.temp_dir_to_cleanup, ignore_errors=True)
        super().closeEvent(event)
