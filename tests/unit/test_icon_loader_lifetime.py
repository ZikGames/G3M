"""Regression tests for asynchronous icon-loader ownership."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QLabel

from ui.common.styling import load_mod_icon_universal
from ui.widgets.mod_details_overlay import ModDetailsOverlay


class _CapturingPool:
    def __init__(self) -> None:
        self.runnable = None

    def start(self, runnable) -> None:
        self.runnable = runnable


def test_icon_loader_signals_outlive_deleted_label(qapp):
    label = QLabel()
    pool = _CapturingPool()
    mod = SimpleNamespace(
        id="remote-mod",
        icon="https://example.com/icon.png",
        icon_path=None,
        screenshots_url=[],
    )

    with patch("ui.utils.image_loader.get_image_loader_pool", return_value=pool):
        load_mod_icon_universal(label, mod)

    assert pool.runnable is not None
    assert pool.runnable.signals.parent() is None
    label.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    pool.runnable.signals.result.emit(QImage())
    qapp.processEvents()


@pytest.mark.parametrize("replacement", ["remote", "local", "empty", "resize"])
def test_superseded_icon_requests_cannot_replace_current_image(qtbot, tmp_path, replacement):
    label = QLabel()
    qtbot.addWidget(label)
    requests = []
    pool = SimpleNamespace(start=requests.append)
    blue = QImage(80, 80, QImage.Format.Format_ARGB32)
    blue.fill(QColor("blue"))
    red = QImage(80, 80, QImage.Format.Format_ARGB32)
    red.fill(QColor("red"))
    fallback = tmp_path / "old-fallback.png"
    assert red.save(str(fallback))
    with patch("ui.utils.image_loader.get_image_loader_pool", return_value=pool):
        load_mod_icon_universal(label, SimpleNamespace(icon="https://example.com/old.png"), local_fallback=str(fallback))
        if replacement in {"remote", "resize"}:
            url = "https://example.com/new.png" if replacement == "remote" else "https://example.com/old.png"
            size = 80 if replacement == "remote" else 120
            load_mod_icon_universal(label, SimpleNamespace(icon=url), size=size)
            requests[1].signals.result.emit(blue)
        elif replacement == "local":
            path = tmp_path / "icon.png"
            assert blue.save(str(path))
            load_mod_icon_universal(label, SimpleNamespace(icon=str(path)))
        else:
            load_mod_icon_universal(label, SimpleNamespace(icon=""))
    current = label.pixmap().toImage()
    requests[0].signals.result.emit(red)
    assert label.pixmap().toImage() == current
    requests[0].signals.error.emit("https://example.com/old.png", "network:Timeout")
    assert label.pixmap().toImage() == current


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _StuckThread:
    finished = _Signal()

    def __init__(self) -> None:
        self.terminated = False

    def blockSignals(self, _blocked) -> None:  # noqa: N802
        pass

    def isRunning(self) -> bool:  # noqa: N802
        return True

    def isFinished(self) -> bool:  # noqa: N802
        return False

    def requestInterruption(self) -> None:  # noqa: N802
        pass

    def quit(self) -> None:
        pass

    def wait(self, _timeout) -> bool:
        return False

    def terminate(self) -> None:
        self.terminated = True

    def deleteLater(self) -> None:  # noqa: N802
        pass


def test_overlay_cleanup_never_force_terminates_running_thread():
    thread = _StuckThread()

    ModDetailsOverlay._stop_thread(thread)

    assert thread.terminated is False
    assert thread.finished.callback is not None


@pytest.mark.parametrize("dimensions", [(16, 16), (40, 20), (512, 1024)])
@pytest.mark.parametrize("remote", [False, True])
def test_fitted_icons_scale_up_or_down_without_cropping(qtbot, tmp_path, dimensions, remote):
    width, height = dimensions
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.green)
    path = tmp_path / "icon.png"
    assert image.save(str(path))
    label = QLabel()
    qtbot.addWidget(label)
    pool = _CapturingPool()
    mod = SimpleNamespace(icon="https://example.com/fit-icon.png" if remote else str(path))
    with patch("ui.utils.image_loader.get_image_loader_pool", return_value=pool):
        load_mod_icon_universal(label, mod, size=76, fit=True)
    if remote:
        assert pool.runnable is not None
        pool.runnable.signals.result.emit(image)
    expected = QPixmap.fromImage(image).scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio)
    assert label.pixmap().size() == expected.size()
    assert max(label.pixmap().width(), label.pixmap().height()) == 76
    if not remote:
        load_mod_icon_universal(label, mod, size=76)
        assert label.pixmap().width() == label.pixmap().height() == 76
        load_mod_icon_universal(label, mod, size=76, fit=True)
        assert label.pixmap().size() == expected.size()
