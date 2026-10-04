"""Dialog for viewing README/text files from a mod folder."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (
    QCloseEvent,
    QFont,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from config.config import MOD_README_HEADING_FONT_FACTORS
from services.localization_service import tr
from ui.common.dialog_theme import (
    DynamicDialog,
    build_dialog_theme_stylesheet,
    get_dialog_theme_values,
    scale_stylesheet,
)
from ui.common.rich_html import set_rich_html
from utils.mod.archive import (
    ArchiveValidationError,
    materialize_archive,
    split_archive_virtual_path,
)
from utils.mod.readme_utils import (
    is_html_file,
    is_markdown_file,
    is_pdf_file,
    read_mod_readme,
)
from utils.native_integration import open_url_native


def _normalize_markdown_source(content: str) -> str:
    lines = []
    in_fence = False
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip(" \t\u00a0")
        if stripped.startswith("\\#"):
            stripped = stripped[1:]
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence and stripped.startswith("#"):
            line = stripped
        lines.append(line)
    return "".join(lines)


def _normalize_markdown_formats(viewer: QTextBrowser) -> None:
    viewer.ensurePolished()
    document = cast(QTextDocument, viewer.document())
    base_size = document.defaultFont().pointSizeF()
    if base_size <= 0:
        pixel_size = document.defaultFont().pixelSize()
        if pixel_size > 0:
            base_size = pixel_size * 72 / viewer.logicalDpiY()
    if base_size <= 0:
        base_size = viewer.font().pointSizeF()
    if base_size <= 0:
        base_size = 9.0
    block = document.begin()
    while block.isValid():
        anchors = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.charFormat().isAnchor():
                anchors.append((fragment.position(), fragment.length()))
            iterator += 1
        for position, length in anchors:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(viewer.palette().color(QPalette.ColorRole.Text))
            fmt.setFontUnderline(True)
            cursor.mergeCharFormat(fmt)
        level = block.blockFormat().headingLevel()
        if level:
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold)
            fmt.setFontPointSize(
                base_size * MOD_README_HEADING_FONT_FACTORS.get(level, 1.0)
            )
            cursor.mergeCharFormat(fmt)
        block = block.next()


class ReadmeFileViewer(QWidget):
    """Lazy document viewer shared by INFO and manual installation."""

    def __init__(self, file_path: str, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self._content_file_path: str | None = None
        self._temporary_directory: TemporaryDirectory[str] | None = None
        self._loaded = False
        self._load_error = False
        self._content: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = None
        self.pdf_viewer = None
        self.pdf_error_label = None
        self._pdf_document = None
        if is_pdf_file(self.file_path):
            self._pdf_document = QPdfDocument(self)
            self.pdf_viewer = QPdfView(self)
            self.pdf_viewer.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            layout.addWidget(self.pdf_viewer)
            self.pdf_error_label = QLabel(self)
            self.pdf_error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.pdf_error_label.hide()
            layout.addWidget(self.pdf_error_label)
            return
        self.viewer = QTextBrowser(self)
        self.viewer.setOpenExternalLinks(False)
        self.viewer.anchorClicked.connect(self._open_link)
        self.viewer.setReadOnly(True)
        layout.addWidget(self.viewer)

    def load_content(self) -> None:
        if self._loaded:
            return
        content_path = self._resolve_content_path()
        if content_path is None:
            self._load_error = True
            if self.viewer:
                self.viewer.setPlainText(tr("status.loading_error"))
            elif self.pdf_viewer and self.pdf_error_label:
                self.pdf_viewer.hide()
                self.pdf_error_label.setText(tr("status.loading_error"))
                self.pdf_error_label.show()
            self._loaded = True
            return
        if self._pdf_document and self.pdf_viewer:
            error = self._pdf_document.load(content_path)
            if (
                error != QPdfDocument.Error.None_
                or self._pdf_document.status() == QPdfDocument.Status.Error
            ):
                self._load_error = True
                if self.pdf_error_label:
                    self.pdf_viewer.hide()
                    self.pdf_error_label.setText(tr("status.loading_error"))
                    self.pdf_error_label.show()
                self._loaded = True
                return
            self.pdf_viewer.setDocument(self._pdf_document)
            self._loaded = True
            return
        try:
            content = read_mod_readme(content_path)
        except OSError:
            self._load_error = True
            if self.viewer:
                self.viewer.setPlainText(tr("status.loading_error"))
            self._loaded = True
            return
        self._content = content
        self._render_content()
        self._loaded = True

    def _render_content(self) -> None:
        if self.viewer is None or self._content is None:
            return
        content = self._content
        self.viewer.ensurePolished()
        cast(QTextDocument, self.viewer.document()).setDefaultFont(self.viewer.font())
        if is_markdown_file(self.file_path):
            self.viewer.setMarkdown(_normalize_markdown_source(content))
            _normalize_markdown_formats(self.viewer)
        elif is_html_file(self.file_path):
            set_rich_html(
                self.viewer,
                content,
                base_path=os.path.dirname(os.path.abspath(self._content_file_path or self.file_path)),
            )
        else:
            self.viewer.setPlainText(content)

    def relocalize_ui(self) -> None:
        if self._load_error:
            if self.viewer:
                self.viewer.setPlainText(tr("status.loading_error"))
            elif self.pdf_error_label:
                self.pdf_error_label.setText(tr("status.loading_error"))

    def apply_theme(self) -> None:
        if self.viewer and self._content is not None:
            cursor = self.viewer.textCursor()
            position, anchor = cursor.position(), cursor.anchor()
            scrollbar = self.viewer.verticalScrollBar()
            scroll = scrollbar.value() if scrollbar else 0
            self._render_content()
            cursor = self.viewer.textCursor()
            cursor.setPosition(anchor)
            cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
            self.viewer.setTextCursor(cursor)
            if scrollbar:
                scrollbar.setValue(scroll)

    def rescale_ui(self) -> None:
        self.apply_theme()

    def unload_content(self) -> None:
        if self.viewer:
            self.viewer.clear()
        if self._pdf_document:
            self._pdf_document.close()
        if self._temporary_directory:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
        self._content_file_path = None
        self._loaded = False
        self._load_error = False
        self._content = None

    def dispose(self) -> None:
        self.unload_content()

    def _open_link(self, url: QUrl) -> None:
        allowed_schemes = {"http", "https", "mailto"}
        if url and url.isValid() and url.scheme().lower() in allowed_schemes:
            open_url_native(url.toString())

    def _resolve_content_path(self) -> str | None:
        if self._content_file_path:
            return self._content_file_path
        try:
            virtual = split_archive_virtual_path(self.file_path)
        except ArchiveValidationError:
            return None
        if virtual is None:
            self._content_file_path = self.file_path
            return self.file_path
        temporary = TemporaryDirectory(prefix="g3m_readme_")
        try:
            materialize_archive(virtual.archive, temporary.name)
            path = Path(temporary.name).joinpath(*virtual.member.split("/"))
            if not path.is_file():
                raise OSError("archive member is not a file")
        except (ArchiveValidationError, OSError, ValueError):
            temporary.cleanup()
            return None
        self._temporary_directory = temporary
        self._content_file_path = str(path)
        return self._content_file_path


class ModReadmeDialog(DynamicDialog):
    """Tabbed README viewer with lazy per-tab loading."""

    def __init__(
        self,
        app_state,
        mod_name: str,
        readme_files: list[str],
        unlisted_files: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._mod_name = mod_name or "Mod"
        self._listed_files = list(readme_files or [])
        self._unlisted_files = list(unlisted_files or [])
        self._current_index = -1
        self._build_ui()
        self.relocalize_ui()
        self.refresh_theme()
        self._rebuild_tabs()

    def _build_ui(self) -> None:
        self.resize(920, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._title_label = QLabel(self)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)

        self._unlisted_checkbox = QCheckBox(self)
        self._unlisted_checkbox.toggled.connect(self._set_show_unlisted)
        layout.addWidget(self._unlisted_checkbox, alignment=Qt.AlignmentFlag.AlignCenter)

        self._content_stack = QStackedWidget(self)
        self._tabs = QTabWidget(self._content_stack)
        self._tabs.setDocumentMode(True)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._empty_page = QWidget(self._content_stack)
        empty_layout = QVBoxLayout(self._empty_page)
        empty_layout.addStretch()
        self._empty_label = QLabel(self._empty_page)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self._empty_label)
        empty_layout.addStretch()
        self._content_stack.addWidget(self._tabs)
        self._content_stack.addWidget(self._empty_page)
        layout.addWidget(self._content_stack, 1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._close_button = QPushButton(self)
        self._close_button.clicked.connect(self.accept)
        button_row.addWidget(self._close_button)
        button_row.addStretch()
        layout.addLayout(button_row)

    def _sync_empty_state(self) -> None:
        self._content_stack.setCurrentWidget(
            self._tabs if self._tabs.count() else self._empty_page
        )

    def _set_show_unlisted(self, _checked: bool) -> None:
        self._rebuild_tabs()

    def _rebuild_tabs(self) -> None:
        self._current_index = -1
        while self._tabs.count():
            tab = self._tabs.widget(0)
            if isinstance(tab, ReadmeFileViewer):
                tab.dispose()
            self._tabs.removeTab(0)
            if tab is not None:
                tab.deleteLater()
        files = list(self._listed_files)
        if self._unlisted_checkbox.isChecked():
            files.extend(self._unlisted_files)
        basename_counts: dict[str, int] = {}
        for file_path in files:
            basename = os.path.basename(file_path)
            basename_counts[basename] = basename_counts.get(basename, 0) + 1
        try:
            common_root = os.path.commonpath(files)
        except ValueError:
            common_root = ""
        for file_path in files:
            label = os.path.basename(file_path)
            if basename_counts[label] > 1:
                label = os.path.relpath(file_path, common_root) if common_root else file_path
            tab = ReadmeFileViewer(file_path, self._tabs)
            self._tabs.addTab(tab, label)
        self._sync_empty_state()
        if self._tabs.count():
            self._tabs.setCurrentIndex(0)
            self._on_tab_changed(0)

    def _on_tab_changed(self, index: int) -> None:
        if self._current_index == index:
            return
        if 0 <= self._current_index < self._tabs.count():
            old_tab = self._tabs.widget(self._current_index)
            if isinstance(old_tab, ReadmeFileViewer):
                old_tab.unload_content()
        self._current_index = index
        if 0 <= index < self._tabs.count():
            new_tab = self._tabs.widget(index)
            if isinstance(new_tab, ReadmeFileViewer):
                new_tab.load_content()

    def refresh_theme(self) -> None:
        theme = get_dialog_theme_values(self._app_state)
        markdown_css = f"""
            body {{
                color: {theme["main_text"]};
                font-size: 14px;
                line-height: 1.45;
            }}
            a {{
                color: {theme["hover"]};
            }}
            pre, code {{
                background-color: {theme["background"]};
                color: {theme["main_text"]};
                border-radius: 8px;
            }}
            pre {{
                padding: 10px;
            }}
            blockquote {{
                border-left: 3px solid {theme["border"]};
                margin-left: 0;
                padding-left: 12px;
                color: {theme["secondary_text"]};
            }}
        """
        self.set_theme_stylesheet(
            build_dialog_theme_stylesheet(self._app_state)
            + f"""
            QLabel {{
                color: {theme["main_text"]};
            }}
            QLabel#readmeTitle {{
                font-size: 18px;
                font-weight: 700;
            }}
            QTabWidget::tab-bar {{
                alignment: center;
                top: 4px;
            }}
            QTabWidget::pane {{
                border: 2px solid {theme["border"]};
                border-radius: {theme["border_radius"]}px;
                background-color: {theme["background"]};
                padding-top: 10px;
                top: -2px;
            }}
            QTabBar::tab {{
                background-color: {theme["elements"]};
                color: {theme["main_text"]};
                border: 2px solid {theme["border"]};
                border-bottom: none;
                padding: 8px 16px;
                margin: 0 4px 6px 4px;
                border-top-left-radius: {theme["button_radius"]}px;
                border-top-right-radius: {theme["button_radius"]}px;
            }}
            QTabBar::tab:selected {{
                background-color: {theme["hover"]};
                margin-bottom: 2px;
            }}
            QTextBrowser {{
                background-color: {theme["elements"]};
                color: {theme["main_text"]};
                border: none;
                padding: 14px;
                selection-background-color: {theme["hover"]};
            }}
            """
        )
        self._title_label.setObjectName("readmeTitle")
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, ReadmeFileViewer) and tab.viewer and is_markdown_file(tab.file_path):
                cast(QTextDocument, tab.viewer.document()).setDefaultStyleSheet(scale_stylesheet(markdown_css, self._app_state))

    def relocalize_ui(self) -> None:
        self.setWindowTitle(tr("dialogs.readme_viewer_title", mod_name=self._mod_name))
        self._title_label.setText(
            tr("dialogs.readme_viewer_title", mod_name=self._mod_name)
        )
        self._empty_label.setText(tr("dialogs.no_readme_files"))
        self._close_button.setText(tr("ui.close_button"))
        self._unlisted_checkbox.setText(
            tr("dialogs.show_unlisted_info_files", count=len(self._unlisted_files))
        )
        self._unlisted_checkbox.setVisible(bool(self._unlisted_files))

    def _unload_tabs(self) -> None:
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, ReadmeFileViewer):
                tab.dispose()

    def done(self, a0: int) -> None:
        result = a0
        self._unload_tabs()
        super().done(result)

    def closeEvent(self, a0) -> None:
        event = cast(QCloseEvent, a0)
        self._unload_tabs()
        super().closeEvent(event)
