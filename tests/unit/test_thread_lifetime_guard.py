import threading

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QWidget

from services.background_operations import background_operations
from ui.utils.thread_lifetime import ManagedQThread, retire_qthread


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _Thread:
    def __init__(self, running: bool) -> None:
        self.finished = _Signal()
        self.running = running
        self.deleted = False

    def isRunning(self):  # noqa: N802
        return self.running

    def deleteLater(self):  # noqa: N802
        self.deleted = True


def test_running_thread_is_retained_until_native_finished():
    thread = _Thread(running=True)

    retire_qthread(thread)

    assert thread.deleted is False
    assert thread.finished.callback is not None
    thread.running = False
    thread.finished.callback()
    assert thread.deleted is True


def test_finished_thread_is_deleted_immediately():
    thread = _Thread(running=False)

    retire_qthread(thread)

    assert thread.deleted is True


def test_thread_finishing_during_signal_registration_is_deleted():
    thread = _Thread(running=True)
    original_connect = thread.finished.connect

    def connect_and_finish(callback):
        original_connect(callback)
        thread.running = False

    thread.finished.connect = connect_and_finish

    retire_qthread(thread)

    assert thread.deleted is True


@pytest.mark.parametrize("retire_before_delete", [False, True])
def test_native_running_thread_survives_parent_deletion(qtbot, retire_before_delete):
    started = threading.Event()
    finish = threading.Event()

    class Worker(ManagedQThread):
        def run(self):
            started.set()
            finish.wait(5)

    parent = QWidget()
    thread = Worker(parent)
    thread.start()
    try:
        assert started.wait(2)
        if retire_before_delete:
            retire_qthread(thread)
        assert thread.parent() is None
        sip.delete(parent)
        assert not sip.isdeleted(thread)
    finally:
        finish.set()
        assert thread.wait(2000)
    qtbot.waitUntil(lambda: id(thread) not in background_operations._threads)
    if not retire_before_delete:
        retire_qthread(thread)
    qtbot.waitUntil(lambda: sip.isdeleted(thread))
