"""The height of a filters strip follows its content and its horizontal scrollbar."""

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea

from ui.common.styling import install_size_hint_height_sync


@pytest.fixture
def strip(qapp):
    area = QScrollArea()
    area.setWidgetResizable(True)
    frame = QFrame()
    frame.setMinimumWidth(800)
    QHBoxLayout(frame).addWidget(QLabel("filters"))
    area.setWidget(frame)
    area.resize(300, 200)
    area.show()
    install_size_hint_height_sync(frame, area, attr_name="_test_height_filter")
    qapp.processEvents()
    yield area, frame
    area.close()
    area.deleteLater()


def test_room_for_the_scrollbar_is_kept_only_while_it_scrolls(strip, qapp):
    area, frame = strip
    bar = area.horizontalScrollBar()
    height = frame.sizeHint().height()

    assert bar.maximum() > 0
    assert area.height() == height + bar.sizeHint().height()

    area.resize(1000, 200)
    qapp.processEvents()

    assert bar.maximum() == 0
    assert area.height() == height


def test_tearing_down_a_scrolling_strip_is_safe(qapp):
    # A Python slot on the scrollbar range ran while the area was being destroyed and crashed Qt.
    for _ in range(20):
        area = QScrollArea()
        area.setWidgetResizable(True)
        frame = QFrame()
        frame.setMinimumWidth(800)
        QHBoxLayout(frame).addWidget(QLabel("filters"))
        area.setWidget(frame)
        area.resize(300, 200)
        area.show()
        install_size_hint_height_sync(frame, area, attr_name="_test_height_filter")
        qapp.processEvents()
        sip.delete(area)
        qapp.processEvents()
