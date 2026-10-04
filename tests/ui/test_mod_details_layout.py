import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from models.mod_models import BrowserModInfo
from ui.widgets.mod_details_overlay import show_mod_details_overlay


@pytest.mark.parametrize("size", [(720, 480), (960, 600), (1440, 900)])
@pytest.mark.parametrize("font_size", [14, 20])
def test_details_actions_stay_visible_above_scrolling_content(qtbot, size, font_size):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(*size)
    host.setStyleSheet(f"QWidget {{ font-size: {font_size}px; }}")
    host.show()
    mod = BrowserModInfo.from_dict(
        {
            "name": "Chapter One music pack",
            "authors": ["Music workshop"],
            "game": "deltarune",
            "full_description": "<p>Battle and exploration tracks.</p>" * 50,
        }
    )
    overlay = show_mod_details_overlay(host, mod)
    qtbot.waitUntil(lambda: overlay.close_button.hasFocus())
    parent_widget = overlay._img_label.parentWidget()
    assert parent_widget is not None
    assert not parent_widget.isVisible()
    for button in (overlay.action_button, overlay.close_button):
        assert button.visibleRegion().boundingRect() == button.rect()
    assert (
        overlay.close_button.x() - overlay.action_button.geometry().right() - 1
    ) >= 8
    qtbot.keyClick(overlay.close_button, Qt.Key.Key_Escape)
    assert overlay.dialog_closed
