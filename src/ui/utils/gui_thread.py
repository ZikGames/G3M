"""Lets a worker thread run a dialog on the GUI thread and wait for the answer."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget


def find_attribute(owner: Any, name: str) -> Any:
    """First non-None ``name`` on ``owner`` or its parents; owners need not be Qt objects."""
    seen = set()
    while owner is not None and id(owner) not in seen:
        seen.add(id(owner))
        if (value := getattr(owner, name, None)) is not None:
            return value
        parent = getattr(owner, "parent", None)
        owner = parent() if callable(parent) else None
    return None


def dialog_parent(owner: QObject | None) -> QWidget | None:
    """The widget a dialog for ``owner`` belongs to, else the active window."""
    while owner is not None and not isinstance(owner, QWidget):
        owner = owner.parent()
    return owner or QApplication.activeWindow()


@dataclass
class _Request:
    function: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    abandoned: bool = False
    result: Any = None


class GuiThreadCall(QObject):
    """Create on the GUI thread; ``call`` may then be used from any worker thread."""

    _requested = pyqtSignal(object)

    def __init__(self, owner: QObject | None = None) -> None:
        super().__init__(owner)
        self._owner = owner
        self._requested.connect(self._run)

    def call(
        self,
        function: Callable[[], Any],
        is_cancelled: Callable[[], bool] = lambda: False,
        headless: Any = None,
    ) -> Any:
        """Block the worker until ``function`` has run on the GUI thread.

        Returns ``None`` when the worker is cancelled first and ``headless`` without a QApplication.
        """
        if QApplication.instance() is None:
            return headless
        request = _Request(function)
        self._requested.emit(request)
        while not request.done.wait(0.1):
            if is_cancelled():
                request.abandoned = True
                return None
        return request.result

    def _run(self, request: _Request) -> None:
        try:
            if not request.abandoned:
                request.result = request.function()
        finally:
            request.done.set()
