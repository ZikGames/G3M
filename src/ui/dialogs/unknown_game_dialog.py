"""What to do with a G3M mod made for a game this installation does not know."""

import logging
from collections.abc import Callable
from typing import Any, cast

from PyQt6.QtWidgets import QMessageBox, QPushButton

from models.game_modes import get_game, get_gamebanana_reverse_map
from services.localization_service import tr
from ui.common.dialog_theme import DynamicMessageBox
from ui.dialogs.custom_game_dialog import CustomGameDialog, run_custom_game_dialog
from ui.utils.gui_thread import GuiThreadCall, dialog_parent, find_attribute

logger = logging.getLogger(__name__)

GAME_READY = "ready"
MANUAL_INSTALL = "manual"


def resolve_unknown_game(
    owner: Any,
    game_id: str,
    mod_name: str,
    gamebanana_id: int | None = None,
) -> str | None:
    """Make sure the mod's game exists: ``GAME_READY``, ``MANUAL_INSTALL`` or ``None`` to cancel.

    A game that is already registered (built in or custom) needs no question. Otherwise the user
    either installs the mod manually as usual or adds the game, using the mod's game id.
    """
    if get_game(game_id) is not None:
        return GAME_READY
    registry = find_attribute(owner, "game_registry_service")
    app_state = find_attribute(owner, "app_state")
    if registry is None or app_state is None or not registry.is_valid_new_game_id(game_id):
        return MANUAL_INSTALL
    parent = dialog_parent(owner)
    box = DynamicMessageBox(parent)
    box.set_localized_title("games.mod_unknown_title")
    box.localize(box.setText, "games.mod_unknown_text", name=mod_name, game=game_id)
    # Qt drops buttons that are added without a title, so they get one before being localized.
    add_button = box.localize_text(
        cast(QPushButton, box.addButton(tr("games.mod_unknown_add"), QMessageBox.ButtonRole.AcceptRole)),
        "games.mod_unknown_add",
    )
    manual_button = box.localize_text(
        cast(QPushButton, box.addButton(tr("games.mod_unknown_manual"), QMessageBox.ButtonRole.ActionRole)),
        "games.mod_unknown_manual",
    )
    box.addButton(QMessageBox.StandardButton.Cancel)
    for button in box.buttons():
        button.setMinimumWidth(button.sizeHint().width() + 18)
    box.exec()
    if box.clickedButton() is manual_button:
        return MANUAL_INSTALL
    if box.clickedButton() is not add_button:
        return None
    # A GameBanana game that is already supported cannot also be the new game's GameBanana game.
    prefilled = None if gamebanana_id in get_gamebanana_reverse_map() else gamebanana_id
    dialog = CustomGameDialog(app_state, parent=parent, game_id=game_id, mod_name=mod_name, gamebanana_id=prefilled)
    saved = run_custom_game_dialog(dialog, lambda values: registry.create_custom_game(**values, game_id=game_id))
    return GAME_READY if saved else None


class WorkerGameResolver(GuiThreadCall):
    """Lets a worker thread ask about an unknown game; create it on the GUI thread."""

    def resolve(
        self,
        game_id: str,
        mod_name: str,
        gamebanana_id: Callable[[], int | None] | None = None,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> str | None:
        """Block the worker until the game is settled; ``None`` means the user cancelled."""
        if get_game(game_id) is not None:
            return GAME_READY
        found = gamebanana_id() if gamebanana_id else None
        return self.call(
            lambda: resolve_unknown_game(self._owner, game_id, mod_name, found),
            is_cancelled,
            headless=MANUAL_INSTALL,
        )
