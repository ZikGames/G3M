"""Dialog for creating or editing a custom game."""

import logging
from collections.abc import Callable
from typing import cast

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from services.game_registry_service import GameRegistryValidationError
from services.localization_service import tr
from ui.common.dialog_theme import (
    DynamicDialog,
    apply_dialog_theme,
    get_dialog_theme_values,
)

logger = logging.getLogger(__name__)


def run_custom_game_dialog(dialog: CustomGameDialog, save: Callable[[dict[str, str]], object]) -> bool:
    """Show ``dialog`` until ``save(values)`` succeeds, keeping the typed text after an error; False on cancel."""
    while dialog.exec() == QDialog.DialogCode.Accepted:
        try:
            save(dialog.values())
        except GameRegistryValidationError as error:
            logger.info("Custom game rejected: %s", error.key)
            QMessageBox.warning(dialog, tr("games.manager_title"), tr(error.key))
            continue
        return True
    return False


class CustomGameDialog(DynamicDialog):
    """Minimal custom-game editor."""

    def __init__(
        self,
        app_state,
        record=None,
        parent=None,
        *,
        game_id: str = "",
        mod_name: str = "",
        gamebanana_id: int | None = None,
    ) -> None:
        """``game_id`` and friends prefill the dialog for a game that an installed mod asks for."""
        super().__init__(parent)
        self.app_state = app_state
        self.record = record
        self.game_id = game_id
        self.mod_name = mod_name
        self.prefilled_gamebanana_id = gamebanana_id
        self._is_editing = record is not None
        self.setWindowTitle(
            tr("games.edit_custom_title") if record else tr("games.add_custom_title")
        )
        self.setModal(True)
        self.setMinimumWidth(915)
        self._build_ui()
        self.apply_theme()

    def apply_theme(self) -> None:
        apply_dialog_theme(self, self.app_state)
        theme = get_dialog_theme_values(self.app_state)
        self.set_theme_stylesheet(
            self._theme_stylesheet
            + f" QLabel#customGameHelpLabel {{ color: {theme['secondary_text']}; font-size: 11px; }}"
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        if self.game_id:
            self.mod_help = QLabel()
            self.mod_help.setObjectName("customGameHelpLabel")
            self.mod_help.setWordWrap(True)
            layout.addWidget(self.mod_help)
            self.game_id_edit = QLineEdit(self.game_id)
            self.game_id_edit.setReadOnly(True)
            form.addRow(tr("games.game_id_label"), self.game_id_edit)
        self.display_name_edit = QLineEdit(
            self.record.display_name
            if self.record
            else self.game_id.replace("_", " ").replace("-", " ").title()
        )
        self.display_name_edit.setPlaceholderText(tr("games.display_name_placeholder"))
        form.addRow(tr("games.display_name_label"), self.display_name_edit)
        if not self.game_id:
            self.game_id_edit = QLineEdit(self.record.id if self.record else "")
            self.game_id_edit.setPlaceholderText(tr("games.game_id_placeholder"))
            form.addRow(tr("games.game_id_label"), self.game_id_edit)
            self.game_id_help = QLabel(tr("games.game_id_help"))
            self.game_id_help.setObjectName("customGameHelpLabel")
            self.game_id_help.setWordWrap(True)
            form.addRow("", self.game_id_help)
        self.primary_executable_edit = QLineEdit(
            self.record.primary_executable if self.record else ""
        )
        self.primary_executable_edit.setPlaceholderText(
            tr("games.primary_executable_placeholder")
        )
        form.addRow(tr("games.primary_executable_label"), self.primary_executable_edit)
        self.executable_help = QLabel(tr("games.primary_executable_help"))
        self.executable_help.setObjectName("customGameHelpLabel")
        self.executable_help.setWordWrap(True)
        form.addRow("", self.executable_help)
        self.data_file_name_edit = QLineEdit(
            self.record.data_file_name if self.record else ""
        )
        self.data_file_name_edit.setPlaceholderText(
            tr("games.data_file_name_placeholder")
        )
        form.addRow(tr("games.data_file_name_label"), self.data_file_name_edit)
        self.data_file_help = QLabel(tr("games.data_file_name_help"))
        self.data_file_help.setObjectName("customGameHelpLabel")
        self.data_file_help.setWordWrap(True)
        form.addRow("", self.data_file_help)
        self.steam_app_id_edit = QLineEdit(
            (self.record.steam_app_id or "") if self.record else ""
        )
        self.steam_app_id_edit.setPlaceholderText(tr("games.steam_app_id_placeholder"))
        form.addRow(tr("games.steam_app_id_label"), self.steam_app_id_edit)
        self.gamebanana_id_edit = QLineEdit(
            str(self.record.gamebanana_id or "")
            if self.record
            else str(self.prefilled_gamebanana_id or "")
        )
        self.gamebanana_id_edit.setPlaceholderText(
            tr("games.gamebanana_id_placeholder")
        )
        form.addRow(tr("games.gamebanana_id_label"), self.gamebanana_id_edit)
        layout.addLayout(form)
        self.form_layout = form
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        for button in self.buttons.buttons():
            button.setMinimumWidth(button.sizeHint().width() + 18)
        layout.addWidget(self.buttons)
        self.relocalize_ui()

    def relocalize_ui(self) -> None:
        self.setWindowTitle(
            tr("games.edit_custom_title")
            if self._is_editing
            else tr("games.add_custom_title")
        )
        if self.game_id:
            self.mod_help.setText(tr("games.add_for_mod_help", name=self.mod_name))
        else:
            self.game_id_edit.setPlaceholderText(tr("games.game_id_placeholder"))
            self.game_id_help.setText(tr("games.game_id_help"))
        if isinstance(label := self.form_layout.labelForField(self.game_id_edit), QLabel):
            label.setText(tr("games.game_id_label"))
        for field, label_key, placeholder_key in (
            (
                self.display_name_edit,
                "games.display_name_label",
                "games.display_name_placeholder",
            ),
            (
                self.primary_executable_edit,
                "games.primary_executable_label",
                "games.primary_executable_placeholder",
            ),
            (
                self.data_file_name_edit,
                "games.data_file_name_label",
                "games.data_file_name_placeholder",
            ),
            (
                self.steam_app_id_edit,
                "games.steam_app_id_label",
                "games.steam_app_id_placeholder",
            ),
            (
                self.gamebanana_id_edit,
                "games.gamebanana_id_label",
                "games.gamebanana_id_placeholder",
            ),
        ):
            label = self.form_layout.labelForField(field)
            if isinstance(label, QLabel):
                label.setText(tr(label_key))
            field.setPlaceholderText(tr(placeholder_key))
        self.executable_help.setText(tr("games.primary_executable_help"))
        self.data_file_help.setText(tr("games.data_file_name_help"))
        cast(QPushButton, self.buttons.button(QDialogButtonBox.StandardButton.Ok)).setText(tr("ui.ok"))
        cast(QPushButton, self.buttons.button(QDialogButtonBox.StandardButton.Cancel)).setText(
            tr("dialogs.cancel")
        )

    def values(self) -> dict[str, str]:
        """The form values; the game ID is only included when this dialog lets the user choose it."""
        values = {
            "display_name": self.display_name_edit.text().strip(),
            "primary_executable": self.primary_executable_edit.text().strip(),
            "data_file_name": self.data_file_name_edit.text().strip(),
            "steam_app_id": self.steam_app_id_edit.text().strip(),
            "gamebanana_id": self.gamebanana_id_edit.text().strip(),
        }
        if not self.game_id:
            values["game_id"] = self.game_id_edit.text().strip().lower()
        return values
