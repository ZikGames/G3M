"""Qt drag-and-drop helpers for lazy file export and multi-item imports."""

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from typing import override

from PyQt6.QtCore import QFileInfo, QMimeData, QPoint, QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QFileIconProvider

logger = logging.getLogger(__name__)


def normalize_local_path(path: str) -> str:
    path = (path or "").strip()
    if len(path) > 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return os.path.normpath(path).replace("\\", "/")


def left_button_is_down() -> bool:
    """Whether the primary mouse button is physically held; always False off Windows."""
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    swapped = bool(user32.GetSystemMetrics(23))  # SM_SWAPBUTTON
    return bool(user32.GetAsyncKeyState(0x02 if swapped else 0x01) & 0x8000)


def file_drag_pixmap(filename: str, font: QFont | None = None, max_text_width: int = 220) -> QPixmap:
    """What the dropped file will look like: its system icon for that extension above its name."""
    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    icon_side = 48
    icon = QFileIconProvider().icon(QFileInfo(filename)).pixmap(QSize(icon_side, icon_side))
    metrics = QFontMetrics(font or QApplication.font())
    text = metrics.elidedText(filename, Qt.TextElideMode.ElideMiddle, max_text_width)
    text_width = metrics.horizontalAdvance(text) + 12
    width = max(icon_side + 8, text_width)
    height = icon_side + metrics.height() + 12
    pixmap = QPixmap(round(width * ratio), round(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.drawPixmap(QPoint((width - icon_side) // 2, 2), icon.scaled(round(icon_side * ratio), round(icon_side * ratio), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    label = QRect(0, icon_side + 6, width, metrics.height() + 4)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 150))
    painter.drawRoundedRect(label, 4, 4)
    painter.setPen(QColor("white"))
    painter.setFont(font or QApplication.font())
    painter.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pixmap


class LazyFileExportMimeData(QMimeData):
    """Create an exported file only once the mouse is released over a drop target.

    While the button is held, targets such as Explorer only peek at the path, so they get an empty
    placeholder immediately. The real export runs when the path is asked for after the release.
    """

    INTERNAL_FORMAT = "application/x-g3m-lazy-file-export"

    def __init__(
        self,
        exporter: Callable[[str], bool],
        filename: str,
        internal_format: str | None = None,
    ) -> None:
        super().__init__()
        self._exporter = exporter
        self._filename = filename or "export.zip"
        self._internal_format = internal_format or self.INTERNAL_FORMAT
        self._temp_dir = None
        self._temp_path = None
        self._materialized = False

    def formats(self) -> list[str]:
        return [self._internal_format, "text/uri-list"]

    @override
    def hasUrls(self) -> bool:
        return True

    def urls(self) -> list[QUrl]:
        if left_button_is_down():
            path = self._reserve_path()
        else:
            path = self._ensure_export_ready()
        return [QUrl.fromLocalFile(path)] if path else []

    @override
    def retrieveData(self, mimetype, preferredType):
        mime_type = mimetype
        meta_type = preferredType
        if mime_type == self._internal_format:
            return b"1"
        if mime_type == "text/uri-list":
            return self.urls()
        return super().retrieveData(mime_type, meta_type)

    def has_materialized_export(self) -> bool:
        return bool(self._temp_path and os.path.exists(self._temp_path))

    def cleanup(self) -> None:
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._temp_path = None
        self._materialized = False

    def cleanup_later(self, delay_ms: int = 120_000) -> None:
        if self.has_materialized_export():
            QTimer.singleShot(delay_ms, self.cleanup)
        else:
            self.cleanup()

    def _reserve_path(self) -> str | None:
        """The path the export will have, with an empty file standing in until it is written."""
        if self._temp_path is None:
            try:
                self._temp_dir = tempfile.mkdtemp(prefix="g3m_export_")
                self._temp_path = os.path.join(self._temp_dir, _sanitize_export_name(self._filename))
                open(self._temp_path, "wb").close()
            except OSError as e:
                logger.warning("Could not reserve export path for %s: %s", self._filename, e)
                self.cleanup()
                return None
        return self._temp_path

    def _ensure_export_ready(self) -> str | None:
        if self._materialized:
            return self._temp_path if self.has_materialized_export() else None
        self._materialized = True
        try:
            if (path := self._reserve_path()) and self._exporter(path) and os.path.exists(path):
                return path
        except Exception as e:
            logger.warning("Lazy file export failed for %s: %s", self._filename, e, exc_info=True)
        self.cleanup()
        return None


def collect_drop_file_paths(mime_data: QMimeData | None) -> list[str]:
    paths = []
    if mime_data is None or not mime_data.hasUrls():
        return paths
    for url in mime_data.urls():
        if not url.isLocalFile():
            continue
        path = normalize_local_path(url.toLocalFile())
        if path and path != "." and os.path.exists(path):
            paths.append(path)
    return paths


def collect_drop_urls(mime_data: QMimeData | None) -> list[str]:
    urls = []
    if mime_data is None:
        return urls
    if mime_data.hasUrls():
        for url in mime_data.urls():
            value = url.toString().strip()
            if value.startswith(("http://", "https://")):
                urls.append(value)
    if mime_data.hasText():
        text = mime_data.text().strip()
        if text.startswith(("http://", "https://")) and text not in urls:
            urls.append(text)
    return urls


def _sanitize_export_name(name: str) -> str:
    clean = "".join(c if c.isalnum() or c in " ._-" else "_" for c in (name or "export.zip")).strip()
    return clean or "export.zip"
