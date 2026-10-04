from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from ui.widgets.shared.custom_title_bar import CustomTitleBar


@pytest.mark.parametrize("menu_name", ["windows", "help"])
def test_title_bar_menu_restores_focus(qtbot, menu_name) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    title_bar = CustomTitleBar(host, SimpleNamespace(local_config={}))
    title_bar.set_localized_texts(
        "Windows",
        "Logs",
        "Support",
        "Help",
        "Changelog",
        "Tour",
        "About",
        "Minimize",
        "Maximize",
        "Restore",
        "Close",
    )
    button = getattr(title_bar, f"{menu_name}_button")
    menu = getattr(title_bar, f"{menu_name}_menu")
    host.show()
    host.activateWindow()

    button.setFocus()
    qtbot.waitUntil(button.hasFocus)
    qtbot.keyClick(button, Qt.Key.Key_Space)
    qtbot.waitUntil(menu.isVisible)
    qtbot.keyClick(menu, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not menu.isVisible())

    qtbot.waitUntil(button.hasFocus)
    assert not button.isDown()
