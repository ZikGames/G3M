from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtWidgets import QWidget

from app.localization_utils import _relocalize_widgets


def _widget(qtbot, hook="relocalize_ui"):
    widget = QWidget()
    qtbot.addWidget(widget)
    vars(widget)[hook] = Mock()
    return widget


def test_relocalizes_every_live_widget_with_supported_contract(qtbot) -> None:
    main = Mock(spec=[])
    dialog = _widget(qtbot)
    card = _widget(qtbot, "update_labels_text")
    hidden = _widget(qtbot)
    hidden.hide()

    with patch(
        "ui.common.live_updates.QApplication.allWidgets",
        return_value=[main, dialog, card, hidden],
    ):
        _relocalize_widgets(main)

    vars(dialog)["relocalize_ui"].assert_called_once_with()
    vars(card)["update_labels_text"].assert_called_once_with()
    vars(hidden)["relocalize_ui"].assert_called_once_with()


def test_broken_widget_does_not_block_other_localization(qtbot) -> None:
    main = Mock(spec=[])
    broken = _widget(qtbot)
    vars(broken)["relocalize_ui"].side_effect = ValueError("broken plugin widget")
    healthy = _widget(qtbot)

    with patch(
        "ui.common.live_updates.QApplication.allWidgets",
        return_value=[broken, healthy],
    ):
        _relocalize_widgets(main)

    vars(healthy)["relocalize_ui"].assert_called_once_with()


def test_attribute_error_is_logged_and_does_not_block_relocalization(caplog, qtbot) -> None:
    main = Mock(spec=[])
    broken = _widget(qtbot)
    vars(broken)["relocalize_ui"].side_effect = AttributeError("missing label")
    healthy = _widget(qtbot)

    with patch(
        "ui.common.live_updates.QApplication.allWidgets",
        return_value=[broken, healthy],
    ):
        _relocalize_widgets(main)

    assert "Failed to relocalize_ui on" in caplog.text
    vars(healthy)["relocalize_ui"].assert_called_once_with()


def test_deleted_widget_error_is_ignored_and_does_not_block_relocalization(
    caplog, qtbot,
) -> None:
    main = Mock(spec=[])
    deleted = QWidget()
    sip.delete(deleted)
    healthy = _widget(qtbot)

    with patch(
        "ui.common.live_updates.QApplication.allWidgets",
        return_value=[deleted, healthy],
    ):
        _relocalize_widgets(main)

    assert "Failed to relocalize_ui on" not in caplog.text
    vars(healthy)["relocalize_ui"].assert_called_once_with()
