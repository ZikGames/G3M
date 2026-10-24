"""The mod summary measures a mod folder on a plain thread and hands the result to the GUI thread."""

import threading

from PyQt6.QtCore import QObject, pyqtSlot

from models.app_state import AppState
from ui.widgets.mod.mod_summary_panel import ModSummaryPanel


class _Receiver(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[tuple[str, int, int]] = []

    @pyqtSlot(str, int)
    def on_size(self, folder, size):
        self.received.append((folder, size, threading.get_ident()))


def test_the_size_reaches_the_gui_thread_through_a_signal(qtbot, tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1500)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 500)
    panel = ModSummaryPanel(AppState())
    qtbot.addWidget(panel)
    receiver = _Receiver()
    panel._size_ready.connect(receiver.on_size)

    panel._start_folder_size_computation(str(tmp_path))
    qtbot.waitUntil(lambda: bool(receiver.received), timeout=3000)

    folder, size, thread_id = receiver.received[0]
    assert (folder, size) == (str(tmp_path), 2000)
    assert thread_id == threading.get_ident()
    assert panel._mod_size_cache[str(tmp_path)] == 2000
    qtbot.waitUntil(lambda: not panel._size_threads, timeout=3000)


def test_a_folder_is_measured_only_once_while_it_is_being_measured(qtbot, tmp_path):
    panel = ModSummaryPanel(AppState())
    qtbot.addWidget(panel)
    started = []

    class _Idle:
        def start(self):
            started.append(True)

    panel._size_threads[str(tmp_path)] = _Idle()

    panel._start_folder_size_computation(str(tmp_path))

    assert started == []
