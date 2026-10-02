"""Choose and monitor GameBanana mod updates for one library profile."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from models.game_modes import get_game
from services.localization_service import tr
from ui.common.dialog_theme import build_dialog_theme_stylesheet


class ModUpdatesDialog(QDialog):
    """A compact batch-update chooser; network work stays in its controller."""

    profile_changed = pyqtSignal(str)
    updates_requested = pyqtSignal(list, bool)

    def __init__(
        self,
        app_state,
        profiles: list[str],
        selected_profile: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._candidates: list[dict[str, Any]] = []
        self._build(profiles, selected_profile)
        self.setStyleSheet(build_dialog_theme_stylesheet(app_state))

    def _build(self, profiles: list[str], selected_profile: str) -> None:
        self.setWindowTitle(tr("mod_updates.title"))
        self.setMinimumSize(840, 430)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        profile_row = QHBoxLayout()
        self._profile_label = QLabel(tr("mod_updates.profile"))
        profile_row.addWidget(self._profile_label)
        self._profile_combo = QComboBox(self)
        for profile in profiles:
            self._profile_combo.addItem(profile, profile)
        index = self._profile_combo.findData(selected_profile)
        if index >= 0:
            self._profile_combo.setCurrentIndex(index)
        self._profile_combo.currentIndexChanged.connect(self._emit_profile_change)
        profile_row.addWidget(self._profile_combo, 1)
        layout.addLayout(profile_row)

        self._status = QLabel(tr("mod_updates.checking"), self)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._outcome = QLabel(self)
        self._outcome.setWordWrap(True)
        self._outcome.setVisible(False)
        layout.addWidget(self._outcome)

        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels([tr("mod_updates.available")])
        self._tree.setRootIsDecorated(True)
        self._tree.setItemsExpandable(True)
        self._tree.setUniformRowHeights(True)
        layout.addWidget(self._tree, 1)

        self._replace_current = QCheckBox(tr("mod_updates.replace_current"), self)
        self._replace_current.setToolTip(tr("mod_updates.replace_current_tooltip"))
        layout.addWidget(self._replace_current)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._update_button = QPushButton(tr("mod_updates.update"), self)
        self._update_button.clicked.connect(self._request_updates)
        self._update_button.setEnabled(False)
        button_row.addWidget(self._update_button)
        self._close_button = QPushButton(tr("common.close"), self)
        self._close_button.clicked.connect(self.reject)
        button_row.addWidget(self._close_button)
        layout.addLayout(button_row)

    def _emit_profile_change(self) -> None:
        profile = self._profile_combo.currentData()
        if isinstance(profile, str):
            self.profile_changed.emit(profile)

    def set_candidates(self, candidates: list[dict[str, Any]]) -> None:
        self._candidates = candidates
        self._tree.clear()
        by_game: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            game = candidate.get("game")
            if isinstance(game, str):
                by_game.setdefault(game, []).append(candidate)
        for game_id in sorted(by_game):
            game = get_game(game_id)
            game_item = QTreeWidgetItem([game.display_label if game else game_id])
            game_item.setFlags(
                game_item.flags()
                | Qt.ItemFlag.ItemIsAutoTristate
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            self._tree.addTopLevelItem(game_item)
            for candidate in sorted(by_game[game_id], key=lambda item: str(item.get("name", "")).casefold()):
                remote = candidate.get("resolved")
                metadata = remote.get("metadata") if isinstance(remote, dict) else {}
                remote_version = metadata.get("version") if isinstance(metadata, dict) else ""
                name = str(candidate.get("name") or candidate.get("id") or "-")
                label = f"{name}  ({candidate.get('version') or '?'} → {remote_version or '?'})"
                child = QTreeWidgetItem([label])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setData(0, Qt.ItemDataRole.UserRole, candidate)
                game_item.addChild(child)
            game_item.setCheckState(0, Qt.CheckState.Checked)
            game_item.setExpanded(True)
        count = sum(len(items) for items in by_game.values())
        self._status.setText(
            tr("mod_updates.none") if not count else tr("mod_updates.found", count=count)
        )
        self._update_button.setEnabled(bool(count))

    def relocalize_ui(self) -> None:
        self.setWindowTitle(tr("mod_updates.title"))
        self._profile_label.setText(tr("mod_updates.profile"))
        self._tree.setHeaderLabels([tr("mod_updates.available")])
        self._replace_current.setText(tr("mod_updates.replace_current"))
        self._replace_current.setToolTip(tr("mod_updates.replace_current_tooltip"))
        self._update_button.setText(tr("mod_updates.update"))
        self._close_button.setText(tr("common.close"))
        states = {}
        for index in range(self._tree.topLevelItemCount()):
            game_item = self._tree.topLevelItem(index)
            for child_index in range(game_item.childCount()):
                child = game_item.child(child_index)
                candidate = child.data(0, Qt.ItemDataRole.UserRole)
                states[(candidate.get("game"), candidate.get("id"))] = child.checkState(0)
        self.set_candidates(self._candidates)
        for index in range(self._tree.topLevelItemCount()):
            game_item = self._tree.topLevelItem(index)
            for child_index in range(game_item.childCount()):
                child = game_item.child(child_index)
                candidate = child.data(0, Qt.ItemDataRole.UserRole)
                state = states.get((candidate.get("game"), candidate.get("id")))
                if state is not None:
                    child.setCheckState(0, state)

    def set_checking(self, *, preserve_outcome: bool = False) -> None:
        self._tree.clear()
        self._status.setText(tr("mod_updates.checking"))
        self._update_button.setEnabled(False)
        if not preserve_outcome:
            self._outcome.clear()
            self._outcome.setVisible(False)

    def set_progress(self, completed: int, total: int, name: str = "") -> None:
        self._progress.setVisible(total > 0)
        self._progress.setValue(int(completed * 100 / max(total, 1)))
        self._status.setText(tr("mod_updates.progress", current=completed, total=total, name=name))

    def set_busy(self, busy: bool) -> None:
        self._profile_combo.setEnabled(not busy)
        self._tree.setEnabled(not busy)
        self._replace_current.setEnabled(not busy)
        self._update_button.setEnabled(not busy and self._tree.topLevelItemCount() > 0)

    def set_error(self, message: str) -> None:
        self._status.setText(message)

    def set_outcome(self, message: str) -> None:
        self._outcome.setText(message)
        self._outcome.setVisible(bool(message))

    def _request_updates(self) -> None:
        selected: list[dict[str, Any]] = []
        for index in range(self._tree.topLevelItemCount()):
            game_item = self._tree.topLevelItem(index)
            for child_index in range(game_item.childCount()):
                child = game_item.child(child_index)
                candidate = child.data(0, Qt.ItemDataRole.UserRole)
                if child.checkState(0) == Qt.CheckState.Checked and isinstance(candidate, dict):
                    selected.append(candidate)
        if selected:
            self.updates_requested.emit(selected, self._replace_current.isChecked())
