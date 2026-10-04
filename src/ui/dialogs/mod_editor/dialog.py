"""Dialog for creating and editing current-format local mods."""

from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
import uuid
import zipfile
from copy import deepcopy
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import cast, override
from urllib.parse import urlparse

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QDrag,
    QDropEvent,
    QIcon,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config.config import CYOP_AFOM_TAG
from models.game_modes import get_game, get_visible_game_entries
from services.localization_service import tr
from ui.common.dialog_theme import DynamicDialog
from ui.common.styling import (
    clamp_border_radius,
    get_border_radius,
    get_theme_color,
    get_ui_scale_factor,
    load_mod_icon_universal,
)
from ui.utils.thread_lifetime import ManagedQThread, retire_qthread
from utils.file_utils import get_file_filter, get_unique_mod_dir
from utils.mod.config import (
    MOD_CONFIG_MAX_AUTHORS,
    MOD_CONFIG_MAX_DESCRIPTION_CHARS,
    MOD_CONFIG_MAX_DISPLAY_CHARS,
    MOD_CONFIG_MAX_GROUP_DEPTH,
    MOD_CONFIG_MAX_PATH_CHARS,
    MOD_CONFIG_MAX_PLACEHOLDERS,
    MOD_CONFIG_MAX_URL_CHARS,
    MOD_CONFIG_VERSION,
    ConfigValidationIssue,
    iter_mod_config_leaves,
    mod_local_relative_path,
    portable_user_path,
    validate_mod_config,
    write_mod_config,
)
from utils.mod.legacy_config_migration import migrate_legacy_config
from utils.mod.operation_plan import (
    ModPathContext,
    build_mod_operation_plan,
    resolved_sha256,
)
from utils.native_integration import (
    get_existing_directory,
    get_open_file_name,
    get_save_file_name,
    open_path_native,
)
from utils.path_utils import (
    colored_icon,
    resolve_execution_runtime,
    resolve_game_executable,
)
from utils.process_utils import format_filesystem_error

logger = logging.getLogger(__name__)

_OPERATION_TYPE_LABEL_KEYS = {
    "patch": "ui.mod_editor_type_patch",
    "overwrite": "ui.mod_editor_type_overwrite",
    "soft-overwrite": "ui.mod_editor_type_soft_overwrite",
    "hard-overwrite": "ui.mod_editor_type_hard_overwrite",
    "extract": "ui.mod_editor_type_extract",
    "soft-extract": "ui.mod_editor_type_soft_extract",
    "hard-extract": "ui.mod_editor_type_hard_extract",
    "info": "ui.mod_editor_type_info",
}
_OPERATION_TYPE_ICON_NAMES = {
    "patch": "operation_patch",
    "overwrite": "operation_overwrite",
    "soft-overwrite": "operation_overwrite",
    "hard-overwrite": "operation_overwrite",
    "extract": "operation_extract",
    "soft-extract": "operation_extract",
    "hard-extract": "operation_extract",
    "info": "operation_info",
}
_OPERATION_TYPE_ORDER = (
    "info",
    "patch",
    "overwrite",
    "extract",
    "soft-overwrite",
    "soft-extract",
    "hard-overwrite",
    "hard-extract",
)
_BUILTIN_PLACEHOLDERS = frozenset(
    {"mod_path", "game_path", "game_data_path", "user_path"}
)
_CUSTOM_PLACEHOLDER_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_HELP_SECTIONS = (
    "metadata",
    "placeholders",
    "custom_placeholders",
    "operations",
    "order",
    "compatibility",
)


def _parse_authors(value: object) -> list[str]:
    return [name.strip() for name in str(value or "").split(",") if name.strip()]


class _OperationTreeWidget(QTreeWidget):
    """Keep the tree and nested config order in sync when entries are dragged."""

    def __init__(self, owner, parent=None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._drag_path: tuple[int, ...] | None = None
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    @override
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        self._drag_path = self._owner._item_path(self.currentItem())
        if self._drag_path is None:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(self.selectedItems()))
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._drag_path = None

    @override
    def dropEvent(self, event: QDropEvent | None) -> None:
        try:
            if event is None or event.source() is not self:
                if event is not None:
                    event.ignore()
                return
            target = self.itemAt(event.position().toPoint())
            source_path = self._drag_path or self._owner._item_path(self.currentItem())
            target_path = self._owner._item_path(target)
            if source_path is None or not self._owner._move_entry(
                source_path, target_path, self.dropIndicatorPosition()
            ):
                event.ignore()
                return
            event.acceptProposedAction()
        finally:
            self._drag_path = None


class _OperationHashThread(ManagedQThread):
    result_ready = pyqtSignal(object, str, int, str, str)

    def __init__(self, path, field, generation, target, parent=None) -> None:
        super().__init__(parent)
        self.path, self.field, self.generation, self.target = path, field, generation, target

    def run(self) -> None:
        try:
            value, error = resolved_sha256(self.target), ""
        except Exception as exc:
            value, error = "", str(exc)
        self.result_ready.emit(self.path, self.field, self.generation, value, error)


class ModEditorDialog(DynamicDialog):
    """Edit one current configuration; historic input is converted at the boundary."""

    _remove_button: QPushButton

    def __init__(self, parent, is_creating: bool = True, mod_data=None) -> None:
        super().__init__(parent)
        self.parent_app, self.is_creating = parent, is_creating
        self._app_state = self._find_app_state(parent)
        payload = mod_data.get("mod_data") if isinstance(mod_data, dict) and isinstance(mod_data.get("mod_data"), dict) else mod_data
        self.mod_data: dict = dict(payload) if isinstance(payload, dict) else {}
        location = {key: self.mod_data[key] for key in ("folder_path", "folder_name") if key in self.mod_data}
        if self.mod_data and self.mod_data.get("config_version") != MOD_CONFIG_VERSION:
            self.mod_data = migrate_legacy_config(self.mod_data, mod_root_path=location.get("folder_path"))
            self.mod_data.update(location)
        self.mod_id = self.mod_data.get("id") if isinstance(self.mod_data.get("id"), str) else None
        self._operation_files = deepcopy(self.mod_data.get("files", []))
        custom_placeholders = self.mod_data.get("placeholders")
        self._custom_placeholders = (
            deepcopy(custom_placeholders)
            if isinstance(custom_placeholders, dict)
            else {}
        )
        self._custom_placeholder_loading = False
        self._loading = False
        self._hash_pending: dict[tuple[tuple[int, ...], str], int] = {}
        self._hash_enabled: set[tuple[tuple[int, ...], str]] = set()
        self._hash_generations: dict[tuple[tuple[int, ...], str], int] = {}
        self._hash_errors: dict[tuple[tuple[int, ...], str], str] = {}
        self._hash_threads: set[_OperationHashThread] = set()
        self._last_browse_dir = os.path.expanduser("~")
        self._cfg = getattr(self._app_state, "local_config", {}) or {}
        self._operation_icons: dict[str, QIcon] = {}
        self._refresh_operation_icons()
        self.resize(1240, 720)
        self.setMinimumSize(700, 500)
        self.setModal(True)
        self._build_ui()
        self._populate()
        self.relocalize_ui()
        self.apply_theme()

    @staticmethod
    def _find_app_state(parent) -> object | None:
        current, visited = parent, set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            state = getattr(current, "app_state", None)
            if state is not None:
                return state
            getter = getattr(current, "parent", None)
            current = getter() if callable(getter) else None
        return None

    def _color(self, key: str, fallback: str) -> str:
        return get_theme_color(self._cfg, key, fallback)

    def _radius(self, width: int = 0, height: int = 0) -> int:
        value = get_border_radius(self._cfg)
        return clamp_border_radius(value, width=width, height=height) if width or height else value

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)

        metadata = QFrame(self._tabs)
        metadata.setObjectName("modEditorFrame")
        metadata_layout = QVBoxLayout(metadata)
        metadata_layout.setContentsMargins(16, 16, 16, 16)
        self._build_metadata(metadata_layout)
        self._tabs.addTab(metadata, tr("ui.mod_editor_tab_metadata"))

        compatibility = QFrame(self._tabs)
        compatibility.setObjectName("modEditorFrame")
        compatibility_layout = QVBoxLayout(compatibility)
        compatibility_layout.setContentsMargins(16, 16, 16, 16)
        self._build_compatibility(compatibility_layout)
        self._tabs.addTab(compatibility, tr("ui.mod_editor_tab_compatibility"))

        operations = QFrame(self._tabs)
        operations.setObjectName("modEditorFrame")
        operation_layout = QVBoxLayout(operations)
        operation_layout.setContentsMargins(16, 16, 16, 16)
        self._files_hint = QLabel(operations)
        self._files_hint.setObjectName("modEditorHint")
        self._files_hint.setWordWrap(True)
        self._files_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        operation_layout.addWidget(self._files_hint)
        operation_layout.addWidget(self._build_operations())
        self._tabs.addTab(operations, tr("ui.mod_editor_tab_files"))

        placeholders = QFrame(self._tabs)
        placeholders.setObjectName("modEditorFrame")
        placeholders_layout = QVBoxLayout(placeholders)
        placeholders_layout.setContentsMargins(16, 16, 16, 16)
        self._build_custom_placeholders(placeholders_layout)
        self._tabs.addTab(placeholders, tr("ui.mod_editor_tab_placeholders"))

        help_page = QFrame(self._tabs)
        help_page.setObjectName("modEditorFrame")
        help_layout = QVBoxLayout(help_page)
        help_layout.setContentsMargins(16, 16, 16, 16)
        self._build_help(help_layout)
        self._tabs.addTab(help_page, tr("ui.mod_editor_tab_help"))
        root.addWidget(self._tabs, 1)
        self._build_actions(root)

    def _build_metadata(self, parent: QVBoxLayout) -> None:
        game_row = QHBoxLayout()
        game_row.addStretch()
        game_row.addWidget(self.localize_text(QLabel(self), "ui.mod_type_label"))
        self.game_combo = QComboBox(self)
        for game in get_visible_game_entries():
            self.game_combo.addItem(game.display_name, game.id)
        game_row.addWidget(self.game_combo)
        game_row.addStretch()
        parent.addLayout(game_row)
        parent.addSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        parent.addLayout(form)

        def field(key: str, edit: QLineEdit) -> None:
            form.addRow(self.localize_text(QLabel(self), key), edit)

        self.name_edit = QLineEdit(self)
        self.name_edit.setMaxLength(MOD_CONFIG_MAX_DISPLAY_CHARS)
        field("ui.mod_name_label", self.name_edit)
        self.authors_edit = QLineEdit(self)
        self.authors_edit.setMaxLength(MOD_CONFIG_MAX_AUTHORS * (MOD_CONFIG_MAX_DISPLAY_CHARS + 2))
        field("ui.mod_editor_authors", self.authors_edit)
        self.description_edit = QLineEdit(self)
        self.description_edit.setMaxLength(MOD_CONFIG_MAX_DESCRIPTION_CHARS)
        field("ui.short_description", self.description_edit)
        self.homepage_edit = QLineEdit(self)
        self.homepage_edit.setMaxLength(MOD_CONFIG_MAX_URL_CHARS)
        field("ui.homepage", self.homepage_edit)
        self.icon_edit = QLineEdit(self)
        self.icon_edit.setMaxLength(MOD_CONFIG_MAX_PATH_CHARS)
        self._icon_preview_timer = QTimer(self)
        self._icon_preview_timer.setSingleShot(True)
        self._icon_preview_timer.setInterval(200)
        self._icon_preview_timer.timeout.connect(lambda: self._load_icon_preview(self.icon_edit.text()))
        self.icon_edit.textChanged.connect(lambda _text: self._icon_preview_timer.start())
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon_edit, 1)
        self.icon_browse_button = QPushButton(self)
        self.icon_browse_button.clicked.connect(self._browse_icon)
        icon_row.addWidget(self.icon_browse_button)
        self.icon_preview = QLabel(self)
        self.icon_preview.setFixedSize(64, 64)
        self.icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_row.addWidget(self.icon_preview)
        form.addRow(self.localize_text(QLabel(self), "files.icon_label"), icon_row)
        tags = QGridLayout()
        self.tag_textedit = self.localize_text(QCheckBox(self), "tags.textedit_text")
        self.tag_customization = self.localize_text(QCheckBox(self), "tags.customization")
        self.tag_gameplay = self.localize_text(QCheckBox(self), "tags.gameplay")
        self.tag_other = self.localize_text(QCheckBox(self), "tags.other")
        for index, checkbox in enumerate((self.tag_textedit, self.tag_customization, self.tag_gameplay, self.tag_other)):
            tags.addWidget(checkbox, index // 2, index % 2)
        form.addRow(self.localize_text(QLabel(self), "ui.mod_tags_label"), tags)
        self.version_edit = QLineEdit(self)
        self.version_edit.setMaxLength(MOD_CONFIG_MAX_DISPLAY_CHARS)
        field("ui.overall_mod_version", self.version_edit)
        self.game_version_edit = QLineEdit(self)
        self.game_version_edit.setMaxLength(MOD_CONFIG_MAX_DISPLAY_CHARS)
        field("ui.game_version_label", self.game_version_edit)

    def _build_compatibility(self, parent: QVBoxLayout) -> None:
        hint = self.localize_text(QLabel(self), "ui.mod_editor_compatibility_hint")
        hint.setObjectName("modEditorHint")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        parent.addWidget(hint)
        self._compatibility_hint = hint
        self._relation_trees: dict[str, QTreeWidget] = {}
        self._relation_id_edits: dict[str, QLineEdit] = {}
        self._relation_mode_combos: dict[str, QComboBox] = {}
        self._relation_titles: dict[str, QLabel] = {}
        self._relation_loading = False
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)
        for field in ("dependencies", "conflicts"):
            pane = QFrame(splitter)
            pane.setObjectName("modEditorOperationPane")
            layout = QVBoxLayout(pane)
            layout.setContentsMargins(20, 0, 20, 16)
            layout.setSpacing(8)
            title = self.localize_text(QLabel(pane), f"ui.mod_editor_{field}")
            title.setObjectName("modEditorPaneTitle")
            layout.addWidget(title)
            tree = QTreeWidget(pane)
            tree.setHeaderLabels(
                [tr("ui.mod_editor_relation_mod_id"), tr("ui.mod_editor_relation_order")]
            )
            tree.setRootIsDecorated(False)
            tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            tree.currentItemChanged.connect(
                lambda _current, _previous, name=field: self._load_relation(name)
            )
            tree.setMinimumWidth(360)
            tree.setMaximumWidth(480)
            tree_row = QHBoxLayout()
            tree_row.addStretch()
            tree_row.addWidget(tree, 1)
            tree_row.addStretch()
            layout.addLayout(tree_row, 1)
            buttons = QHBoxLayout()
            buttons.addStretch()
            add = self.localize_text(QPushButton(pane), "ui.add")
            add.clicked.connect(lambda _checked=False, name=field: self._add_relation(name))
            buttons.addWidget(add)
            remove = self.localize_text(QPushButton(pane), "ui.remove")
            remove.clicked.connect(
                lambda _checked=False, name=field: self._remove_relation(name)
            )
            buttons.addWidget(remove)
            buttons.addStretch()
            layout.addLayout(buttons)
            form_widget = QWidget(pane)
            form_widget.setMinimumWidth(480)
            form_widget.setMaximumWidth(560)
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setLabelAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            relation_id = QLineEdit(pane)
            relation_id.setMaxLength(64)
            relation_id.setPlaceholderText(tr("ui.mod_editor_relation_mod_id_placeholder"))
            relation_id.editingFinished.connect(
                lambda name=field: self._save_relation(name)
            )
            form.addRow(self.localize_text(QLabel(self), "ui.mod_editor_relation_mod_id"), relation_id)
            mode = QComboBox(pane)
            self._populate_relation_modes(mode)
            mode.currentIndexChanged.connect(
                lambda _index, name=field: self._save_relation(name)
            )
            form.addRow(self.localize_text(QLabel(self), "ui.mod_editor_relation_order"), mode)
            form_widget.setLayout(form)
            form_row = QHBoxLayout()
            form_row.addStretch()
            form_row.addWidget(form_widget)
            form_row.addStretch()
            layout.addLayout(form_row)
            self._relation_trees[field] = tree
            self._relation_id_edits[field] = relation_id
            self._relation_mode_combos[field] = mode
            self._relation_titles[field] = title
            splitter.addWidget(pane)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        parent.addWidget(splitter, 1)

    def _build_custom_placeholders(self, parent: QVBoxLayout) -> None:
        self._custom_placeholders_hint = self.localize_text(QLabel(self), "ui.mod_editor_custom_placeholders_hint")
        self._custom_placeholders_hint.setObjectName("modEditorHint")
        self._custom_placeholders_hint.setWordWrap(True)
        self._custom_placeholders_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        parent.addWidget(self._custom_placeholders_hint)

        pane = QFrame(self)
        pane.setObjectName("modEditorOperationPane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(20, 0, 20, 16)
        layout.setSpacing(8)
        self._custom_placeholders_tree = QTreeWidget(pane)
        self._custom_placeholders_tree.setHeaderLabels(
            [
                tr("ui.mod_editor_placeholder_name"),
                tr("ui.mod_editor_placeholder_path"),
            ]
        )
        self._custom_placeholders_tree.setRootIsDecorated(False)
        self._custom_placeholders_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._custom_placeholders_tree.currentItemChanged.connect(
            lambda *_: self._load_custom_placeholder()
        )
        self._custom_placeholders_tree.setMinimumWidth(520)
        self._custom_placeholders_tree.setMaximumWidth(760)
        tree_row = QHBoxLayout()
        tree_row.addStretch()
        tree_row.addWidget(self._custom_placeholders_tree, 1)
        tree_row.addStretch()
        layout.addLayout(tree_row, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        add = self.localize_text(QPushButton(pane), "ui.add")
        add.clicked.connect(self._add_custom_placeholder)
        buttons.addWidget(add)
        remove = self.localize_text(QPushButton(pane), "ui.remove")
        remove.clicked.connect(self._remove_custom_placeholder)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)

        form_widget = QWidget(pane)
        form_widget.setMinimumWidth(520)
        form_widget.setMaximumWidth(680)
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._custom_placeholder_name = QLineEdit(form_widget)
        self._custom_placeholder_name.setMaxLength(64)
        self._custom_placeholder_name.setPlaceholderText(
            tr("ui.mod_editor_placeholder_name_placeholder")
        )
        self._custom_placeholder_name.editingFinished.connect(
            self._save_custom_placeholder
        )
        form.addRow(
            self.localize_text(QLabel(self), "ui.mod_editor_placeholder_name"), self._custom_placeholder_name
        )
        self._custom_placeholder_path = QLineEdit(form_widget)
        self._custom_placeholder_path.setMaxLength(MOD_CONFIG_MAX_PATH_CHARS)
        self._custom_placeholder_path.setPlaceholderText(
            tr("ui.mod_editor_placeholder_path_placeholder")
        )
        self._custom_placeholder_path.editingFinished.connect(
            self._save_custom_placeholder
        )
        form.addRow(
            self.localize_text(QLabel(self), "ui.mod_editor_placeholder_path"), self._custom_placeholder_path
        )
        form_row = QHBoxLayout()
        form_row.addStretch()
        form_row.addWidget(form_widget)
        form_row.addStretch()
        layout.addLayout(form_row)
        parent.addWidget(pane, 1)

    def _build_help(self, parent: QVBoxLayout) -> None:
        self._help_tabs = QTabWidget(self)
        self._help_tabs.setDocumentMode(True)
        self._help_sections: dict[str, QTextBrowser] = {}
        for section in _HELP_SECTIONS:
            text = QTextBrowser(self._help_tabs)
            text.setObjectName("modEditorHelpText")
            text.setOpenExternalLinks(False)
            self._help_sections[section] = text
            title = tr(f"ui.mod_editor_help_{section}_title")
            self._help_tabs.addTab(text, title.replace("&", "&&"))
        parent.addWidget(self._help_tabs, 1)

    def _placeholder_examples_html(self) -> str:
        context = self._context()
        roots = (
            ("${mod_path}", context.mod_path if context else None),
            ("${game_path}", context.game_path if context else None),
            ("${game_data_path}", context.game_data_path if context else None),
            ("${user_path}", context.user_path if context else Path.home()),
        )
        rows = "".join(
            tr(
                "ui.mod_editor_help_placeholder_example",
                placeholder=escape(placeholder),
                path=escape(
                    str(path) if path is not None else tr("ui.mod_editor_help_path_unavailable")
                ),
            )
            for placeholder, path in roots
        )
        return tr("ui.mod_editor_help_placeholder_examples", rows=rows)

    def _build_operations(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        for attribute, key, callback in (
            ("_add_file_button", "ui.add", self._add_file),
            ("_add_group_button", "ui.mod_editor_add_group", self._add_group),
            ("_remove_button", "ui.remove", self._remove),
        ):
            button = self.localize_text(QPushButton(widget), key)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            setattr(self, attribute, button)
        buttons.addStretch()
        layout.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal, widget)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)
        self._operation_splitter = splitter

        tree_pane = QFrame()
        tree_pane.setObjectName("modEditorOperationPane")
        tree_layout = QVBoxLayout(tree_pane)
        tree_layout.setContentsMargins(2, 2, 2, 2)
        tree_layout.setSpacing(0)
        self._operation_tree_title = QLabel(tree_pane)
        self._operation_tree_title.setObjectName("modEditorPaneTitle")
        tree_layout.addWidget(self._operation_tree_title)
        self._tree = _OperationTreeWidget(self, tree_pane)
        self._tree.setObjectName("modEditorOperationTree")
        self._tree.setColumnCount(3)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(18)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tree.setUniformRowHeights(True)
        self._tree.setColumnWidth(0, 34)
        self._tree.setColumnWidth(1, 30)
        self._tree.currentItemChanged.connect(self._load_entry)
        tree_layout.addWidget(self._tree, 1)

        inspector = QFrame()
        inspector.setObjectName("modEditorOperationPane")
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(2, 2, 2, 2)
        inspector_layout.setSpacing(0)
        self._operation_inspector_title = QLabel(inspector)
        self._operation_inspector_title.setObjectName("modEditorPaneTitle")
        inspector_layout.addWidget(self._operation_inspector_title)
        inspector_body = QWidget(inspector)
        inspector_body_layout = QVBoxLayout(inspector_body)
        inspector_body_layout.setContentsMargins(12, 12, 12, 12)
        inspector_body_layout.setSpacing(8)
        self._form = QFormLayout()
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._form_labels: dict[str, QLabel] = {}

        def form_row(key: str, widget: QWidget) -> None:
            label = self.localize_text(QLabel(inspector_body), key)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._form_labels[key] = label
            self._form.addRow(label, widget)

        self._group_name = QLineEdit(inspector_body)
        self._group_name.setMaxLength(MOD_CONFIG_MAX_DISPLAY_CHARS)
        self._group_name.editingFinished.connect(self._rename_group)
        form_row("ui.mod_editor_group", self._group_name)
        self._type = QComboBox(inspector_body)
        self._type.setIconSize(QSize(18, 18))
        self._populate_operation_types()
        self._type.currentIndexChanged.connect(self._save_entry)
        form_row("ui.mod_editor_type", self._type)
        self._source_hash_box = self.localize_text(QCheckBox(inspector_body), "ui.mod_editor_include_source_hash")
        self._source_hash_box.toggled.connect(lambda enabled: self._toggle_hash("source_hash", enabled))
        self._form.addRow(self._source_hash_box)
        self._source = QLineEdit(inspector_body)
        self._source.editingFinished.connect(self._save_entry)
        self._source_row = self._path_row(self._source, self._browse_source)
        form_row("ui.mod_editor_source", self._source_row)
        self._source_hash = QLineEdit(inspector_body)
        self._source_hash.setReadOnly(True)
        form_row("ui.mod_editor_source_hash", self._source_hash)
        self._target_hash_box = self.localize_text(QCheckBox(inspector_body), "ui.mod_editor_include_target_hash")
        self._target_hash_box.toggled.connect(lambda enabled: self._toggle_hash("target_hash", enabled))
        self._form.addRow(self._target_hash_box)
        self._target = QLineEdit(inspector_body)
        self._target.editingFinished.connect(self._save_entry)
        self._target_row = self._path_row(self._target, self._browse_target)
        form_row("ui.mod_editor_target", self._target_row)
        self._target_hash = QLineEdit(inspector_body)
        self._target_hash.setReadOnly(True)
        form_row("ui.mod_editor_target_hash", self._target_hash)
        inspector_body_layout.addLayout(self._form)
        self._validation = QLabel(inspector_body)
        self._validation.setObjectName("modEditorValidation")
        self._validation.setWordWrap(True)
        inspector_body_layout.addWidget(self._validation)
        inspector_body_layout.addStretch()
        inspector_layout.addWidget(inspector_body, 1)

        tree_pane.setMinimumWidth(250)
        inspector.setMinimumWidth(320)
        splitter.addWidget(tree_pane)
        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes((420, 580))
        layout.addWidget(splitter, 1)
        return widget

    def _path_row(self, edit: QLineEdit, callback) -> QWidget:
        row = QWidget(edit.parentWidget())
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        button = QPushButton(row)
        button.setFixedWidth(30)
        button.clicked.connect(callback)
        layout.addWidget(button)
        if edit is self._source:
            self._source_browse = button
        else:
            self._target_browse = button
        return row

    @staticmethod
    def _split_relation(value: object) -> tuple[str, str]:
        relation_id, separator, mode = str(value or "").partition(":")
        return relation_id, mode if separator else ""

    @staticmethod
    def _relation_value(relation_id: str, mode: str) -> str:
        return f"{relation_id}:{mode}" if mode else relation_id

    @staticmethod
    def _normalize_relation_id(value: str) -> str:
        candidate = value.strip()
        try:
            parsed = urlparse(candidate)
        except ValueError:
            return candidate
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() not in {
            "gamebanana.com",
            "www.gamebanana.com",
        }:
            return candidate
        parts = [part for part in parsed.path.split("/") if part]
        item_type = parts[0].casefold() if parts else ""
        if len(parts) == 2 and item_type in {"mods", "wips"} and parts[1].isdigit():
            return f"gb_{item_type[:-1]}_{parts[1]}"
        return candidate

    @staticmethod
    def _relation_mode_key(mode: str) -> str:
        return "ui.mod_editor_relation_order_none" if not mode else (
            f"ui.mod_editor_relation_order_{mode.replace('-', '_')}"
        )

    def _populate_relation_modes(self, combo: QComboBox) -> None:
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for mode in ("", "before", "after", "before-step", "after-step", "before-priority", "after-priority"):
            combo.addItem(tr(self._relation_mode_key(mode)), mode)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def _load_relation(self, field: str) -> None:
        if self._relation_loading:
            return
        tree = self._relation_trees[field]
        item = tree.currentItem()
        relation_id, mode = self._split_relation(
            item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        )
        self._relation_loading = True
        try:
            self._relation_id_edits[field].setText(relation_id)
            combo = self._relation_mode_combos[field]
            combo.setCurrentIndex(max(0, combo.findData(mode)))
            enabled = item is not None
            self._relation_id_edits[field].setEnabled(enabled)
            combo.setEnabled(enabled)
        finally:
            self._relation_loading = False
        self._validate()

    def _save_relation(self, field: str) -> None:
        if self._relation_loading:
            return
        tree = self._relation_trees[field]
        item = tree.currentItem()
        if item is None:
            return
        relation_id = self._normalize_relation_id(self._relation_id_edits[field].text())
        self._relation_id_edits[field].setText(relation_id)
        mode = str(self._relation_mode_combos[field].currentData() or "")
        value = self._relation_value(relation_id, mode)
        item.setData(0, Qt.ItemDataRole.UserRole, value)
        item.setText(0, relation_id)
        item.setText(1, self._relation_mode_combos[field].currentText())
        self._validate()

    def _add_relation(self, field: str) -> None:
        tree = self._relation_trees[field]
        item = QTreeWidgetItem(["", tr("ui.mod_editor_relation_order_none")])
        item.setData(0, Qt.ItemDataRole.UserRole, "")
        tree.addTopLevelItem(item)
        tree.setCurrentItem(item)
        self._relation_id_edits[field].setFocus()

    def _remove_relation(self, field: str) -> None:
        tree = self._relation_trees[field]
        if (item := tree.currentItem()) is not None:
            tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        self._load_relation(field)

    def _relation_values(self, field: str) -> list[str]:
        tree = self._relation_trees[field]
        return [
            str(cast(QTreeWidgetItem, tree.topLevelItem(index)).data(0, Qt.ItemDataRole.UserRole) or "")
            for index in range(tree.topLevelItemCount())
        ]

    def _refresh_custom_placeholders(self, selected: str | None = None) -> None:
        self._custom_placeholders_tree.blockSignals(True)
        self._custom_placeholders_tree.clear()
        selected_item = None
        for name, value in (
            self._custom_placeholders.items()
            if isinstance(self._custom_placeholders, dict)
            else ()
        ):
            item = QTreeWidgetItem([str(name), str(value)])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            self._custom_placeholders_tree.addTopLevelItem(item)
            if name == selected:
                selected_item = item
        if selected_item is None and self._custom_placeholders_tree.topLevelItemCount():
            selected_item = self._custom_placeholders_tree.topLevelItem(0)
        self._custom_placeholders_tree.setCurrentItem(selected_item)
        self._custom_placeholders_tree.blockSignals(False)
        self._load_custom_placeholder()

    def _load_custom_placeholder(self) -> None:
        item = self._custom_placeholders_tree.currentItem()
        name = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._custom_placeholder_loading = True
        try:
            self._custom_placeholder_name.setText(str(name) if name is not None else "")
            self._custom_placeholder_path.setText(
                str(self._custom_placeholders.get(name, ""))
                if isinstance(self._custom_placeholders, dict) and name is not None
                else ""
            )
            self._custom_placeholder_name.setEnabled(name is not None)
            self._custom_placeholder_path.setEnabled(name is not None)
        finally:
            self._custom_placeholder_loading = False
        self._validate()

    def _add_custom_placeholder(self) -> None:
        if (
            not isinstance(self._custom_placeholders, dict)
            or len(self._custom_placeholders) >= MOD_CONFIG_MAX_PLACEHOLDERS
        ):
            return
        index, name = 1, "placeholder"
        while name in self._custom_placeholders:
            index += 1
            name = f"placeholder_{index}"
        self._custom_placeholders[name] = "${mod_path}/folder"
        self._refresh_custom_placeholders(name)
        self._custom_placeholder_name.setFocus()
        self._custom_placeholder_name.selectAll()

    def _remove_custom_placeholder(self) -> None:
        item = self._custom_placeholders_tree.currentItem()
        name = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(self._custom_placeholders, dict) and name in self._custom_placeholders:
            del self._custom_placeholders[name]
        self._refresh_custom_placeholders()

    def _save_custom_placeholder(self) -> None:
        if self._custom_placeholder_loading or not isinstance(
            self._custom_placeholders, dict
        ):
            return
        item = self._custom_placeholders_tree.currentItem()
        old_name = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if old_name not in self._custom_placeholders:
            return
        name = self._custom_placeholder_name.text().strip()
        if not _CUSTOM_PLACEHOLDER_NAME_RE.fullmatch(name):
            message = tr("ui.mod_editor_placeholder_name_invalid")
        elif name.casefold() in _BUILTIN_PLACEHOLDERS:
            message = tr("ui.mod_editor_placeholder_name_reserved")
        elif any(
            name.casefold() == existing.casefold()
            for existing in self._custom_placeholders
            if existing != old_name
        ):
            message = tr("ui.mod_editor_placeholder_name_taken")
        else:
            message = ""
        if message:
            self._safe_warning(
                self,
                tr("ui.mod_editor_tab_placeholders"),
                message,
            )
            self._custom_placeholder_name.setText(str(old_name))
            return
        value = self._custom_placeholder_path.text().strip()
        del self._custom_placeholders[old_name]
        self._custom_placeholders[name] = value
        self._refresh_custom_placeholders(name)
        self._load_icon_preview(self.icon_edit.text())

    def _populate_relations(self, field: str, values: object) -> None:
        tree = self._relation_trees[field]
        tree.blockSignals(True)
        tree.clear()
        for value in values if isinstance(values, list) else ():
            relation_id, mode = self._split_relation(value)
            item = QTreeWidgetItem(
                [relation_id, tr(self._relation_mode_key(mode))]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, str(value))
            tree.addTopLevelItem(item)
        tree.blockSignals(False)
        self._load_relation(field)

    def _entries(self, parent_path: tuple[int, ...]) -> list[object]:
        entries = self._operation_files
        for index in parent_path:
            entry = entries[index]
            if not isinstance(entry, dict) or len(entry) != 1:
                raise ValueError("invalid group")
            entries = next(iter(entry.values()))
            if not isinstance(entries, list):
                raise ValueError("invalid group")
        return entries

    def _path(self) -> tuple[int, ...] | None:
        return self._item_path(self._tree.currentItem())

    @staticmethod
    def _item_path(item: QTreeWidgetItem | None) -> tuple[int, ...] | None:
        raw = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        try:
            return tuple(int(part) for part in raw.split("/")) if raw else None
        except ValueError:
            return None

    def _entry(self, path: tuple[int, ...]) -> dict:
        value = self._entries(path[:-1])[path[-1]]
        if not isinstance(value, dict):
            raise ValueError("invalid entry")
        return value

    @staticmethod
    def _group(entry: dict) -> bool:
        return "source" not in entry and "type" not in entry and len(entry) == 1

    @staticmethod
    def _entry_display_name(source: str, target: str) -> str:
        return target or source or "-"

    @staticmethod
    def _operation_type_label(operation_type: str) -> str:
        key = _OPERATION_TYPE_LABEL_KEYS.get(operation_type)
        return tr(key) if key else operation_type

    def _operation_icon_color(self, operation_type: str) -> str:
        main_text = self._color("main_text", "#e8e9eb")
        if not operation_type.startswith(("soft-", "hard-")):
            return main_text
        base = QColor(main_text)
        if not base.isValid():
            return main_text
        hue = base.hsvHueF()
        if hue < 0 or base.hsvSaturationF() < 0.12:
            hue = 0.0
        offset = 0.36 if operation_type.startswith("soft-") else 0.62
        color = QColor.fromHsvF(
            (hue + offset) % 1.0,
            max(0.55, base.hsvSaturationF()),
            max(0.72, base.valueF()),
            base.alphaF(),
        )
        return color.name()

    def _refresh_operation_icons(self) -> None:
        self._operation_icons = {
            operation_type: colored_icon(
                icon_name, self._operation_icon_color(operation_type)
            )
            for operation_type, icon_name in _OPERATION_TYPE_ICON_NAMES.items()
        }

    def _operation_icon(self, operation_type: str) -> QIcon:
        return self._operation_icons.get(operation_type, QIcon())

    def _populate_operation_types(self) -> None:
        selected = self._type.currentData()
        self._type.blockSignals(True)
        self._type.clear()
        for operation_type in _OPERATION_TYPE_ORDER:
            self._type.addItem(
                self._operation_icon(operation_type),
                self._operation_type_label(operation_type),
                operation_type,
            )
        self._type.setCurrentIndex(
            max(0, self._type.findData(selected))
        )
        self._type.blockSignals(False)

    def _refresh_tree(self, selected: tuple[int, ...] | None = None) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()
        first_leaf: QTreeWidgetItem | None = None
        operation_number = 0

        def add(entries: list[object], parent: QTreeWidgetItem | None, base: tuple[int, ...]) -> None:
            nonlocal first_leaf, operation_number
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                path = (*base, index)
                if self._group(entry):
                    name, children = next(iter(entry.items()))
                    item = QTreeWidgetItem([str(name)])
                    item.setFirstColumnSpanned(True)
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    item.setForeground(0, QBrush(QColor(self._color("secondary_text", "#6de985"))))
                    if isinstance(children, list):
                        add(children, item, path)
                else:
                    source, target = str(entry.get("source", "")), str(entry.get("target", ""))
                    operation_type = str(entry.get("type", ""))
                    operation_number += 1
                    item = QTreeWidgetItem(
                        [
                            str(operation_number),
                            "",
                            self._entry_display_name(source, target),
                        ]
                    )
                    item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
                    item.setIcon(1, self._operation_icon(operation_type))
                    item.setToolTip(
                        2,
                        f"[{operation_type}] {source}"
                        + (f" → {target}" if target else ""),
                    )
                    if first_leaf is None:
                        first_leaf = item
                item.setData(0, Qt.ItemDataRole.UserRole, "/".join(map(str, path)))
                (self._tree.addTopLevelItem if parent is None else parent.addChild)(item)
                if path == selected:
                    self._tree.setCurrentItem(item)

        add(self._operation_files, None, ())
        if selected is None and first_leaf is not None:
            self._tree.setCurrentItem(first_leaf)
        self._tree.expandAll()
        self._tree.blockSignals(False)
        self._load_entry()

    def _load_entry(self) -> None:
        self._loading = True
        try:
            path = self._path()
            entry = self._entry(path) if path is not None else {}
            group = self._group(entry) if path is not None else False
            leaf, info = path is not None and not group, bool(path is not None and not group and entry.get("type") == "info")
            self._operation_inspector_title.setText(
                tr("ui.mod_editor_group") if group else tr("ui.mod_editor_operation")
            )
            self._group_name.setEnabled(group)
            for widget in (self._type, self._source, self._source_browse, self._target, self._target_browse):
                widget.setEnabled(leaf)
            self._group_name.setText(str(next(iter(entry))) if group else "")
            self._source.setText(str(entry.get("source", "")) if leaf else "")
            self._target.setText(str(entry.get("target", "")) if leaf else "")
            operation_type = str(entry.get("type", "overwrite")) if leaf else "overwrite"
            self._type.setCurrentIndex(
                max(0, self._type.findData(operation_type))
            )
            source_hash = bool(path is not None and leaf and ("source_hash" in entry or (path, "source_hash") in self._hash_enabled))
            target_hash = bool(path is not None and leaf and not info and ("target_hash" in entry or (path, "target_hash") in self._hash_enabled))
            self._source_hash_box.setChecked(source_hash)
            self._target_hash_box.setChecked(target_hash)
            self._source_hash.setText(str(entry.get("source_hash", "")) if leaf else "")
            self._target_hash.setText(str(entry.get("target_hash", "")) if leaf else "")
            for widget, visible in ((self._group_name, group), (self._type, leaf), (self._source_hash_box, leaf), (self._source_row, leaf), (self._source_hash, source_hash), (self._target_hash_box, leaf and not info), (self._target_row, leaf and not info), (self._target_hash, target_hash)):
                self._form.setRowVisible(widget, visible)
            for field, widget in (("source_hash", self._source_hash), ("target_hash", self._target_hash)):
                error = self._hash_errors.get((path, field), "") if path is not None else ""
                widget.setToolTip(error)
                widget.setPlaceholderText(error or tr("ui.mod_editor_hash_pending"))
        finally:
            self._loading = False
        self._update_operation_actions()
        self._validate()

    def _update_operation_actions(self) -> None:
        self._remove_button.setEnabled(self._path() is not None)

    def _invalidate_hashes(self) -> None:
        self._hash_enabled.clear()
        self._hash_errors.clear()
        self._hash_pending.clear()
        self._hash_generations = {
            key: generation + 1
            for key, generation in self._hash_generations.items()
        }

    def _add_file(self) -> None:
        path = self._path()
        parent = path if path is not None and self._group(self._entry(path)) else path[:-1] if path else ()
        entries = self._entries(parent)
        index = len(entries) if path is None or parent == path else path[-1] + 1
        self._invalidate_hashes()
        entries.insert(index, {"source": "${mod_path}/", "target": "${game_path}/", "type": "overwrite"})
        self._refresh_tree((*parent, index))

    def _add_group(self) -> None:
        path = self._path()
        parent = path if path is not None and self._group(self._entry(path)) else path[:-1] if path else ()
        if not self._can_create_group_at(parent) or (name := self._prompt_group_name()) is None:
            return
        entries = self._entries(parent)
        index = len(entries) if path is None or parent == path else path[-1] + 1
        self._invalidate_hashes()
        entries.insert(index, {name: []})
        self._refresh_tree((*parent, index))

    def _remove(self) -> None:
        if (path := self._path()) is not None:
            self._invalidate_hashes()
            del self._entries(path[:-1])[path[-1]]
            self._refresh_tree()

    def _find_entry_path(self, entry: dict) -> tuple[int, ...] | None:
        def visit(entries: list[object], base: tuple[int, ...]) -> tuple[int, ...] | None:
            for index, candidate in enumerate(entries):
                path = (*base, index)
                if candidate is entry:
                    return path
                if isinstance(candidate, dict) and self._group(candidate):
                    children = next(iter(candidate.values()))
                    if isinstance(children, list) and (found := visit(children, path)):
                        return found
            return None

        return visit(self._operation_files, ())

    def _group_name_available(self, name: str, ignored: dict | None = None) -> bool:
        normalized = unicodedata.normalize("NFC", name)

        def visit(entries: list[object]) -> bool:
            for entry in entries:
                if not isinstance(entry, dict) or not self._group(entry):
                    continue
                group_name, children = next(iter(entry.items()))
                if entry is not ignored and unicodedata.normalize("NFC", str(group_name)) == normalized:
                    return False
                if isinstance(children, list) and not visit(children):
                    return False
            return True

        return visit(self._operation_files)

    def _prompt_group_name(self) -> str | None:
        while True:
            name, accepted = QInputDialog.getText(
                self, tr("ui.mod_editor_add_group"), tr("ui.mod_editor_group_name")
            )
            if not accepted:
                return None
            name = name.strip()
            if not name or len(name) > MOD_CONFIG_MAX_DISPLAY_CHARS:
                self._safe_warning(
                    self,
                    tr("ui.mod_editor_add_group"),
                    tr("ui.mod_editor_validation_invalid"),
                )
            elif self._group_name_available(name):
                return name
            else:
                self._safe_warning(
                    self,
                    tr("ui.mod_editor_add_group"),
                    tr("ui.mod_editor_group_name_taken"),
                )

    def _can_create_group_at(self, parent_path: tuple[int, ...]) -> bool:
        if len(parent_path) + 1 < MOD_CONFIG_MAX_GROUP_DEPTH:
            return True
        self._safe_warning(
            self,
            tr("ui.mod_editor_add_group"),
            tr("ui.mod_editor_group_depth_limit", limit=MOD_CONFIG_MAX_GROUP_DEPTH - 1),
        )
        return False

    def _group_depth(self, entry: dict) -> int:
        if not self._group(entry):
            return 0
        children = next(iter(entry.values()))
        if not isinstance(children, list):
            return 1
        return 1 + max(
            (self._group_depth(child) for child in children if isinstance(child, dict)),
            default=0,
        )

    def _move_entry(
        self,
        source_path: tuple[int, ...],
        target_path: tuple[int, ...] | None,
        position: QAbstractItemView.DropIndicatorPosition,
    ) -> bool:
        if target_path is not None and target_path[: len(source_path)] == source_path:
            return False
        source_entries = self._entries(source_path[:-1])
        source_index = source_path[-1]
        if not 0 <= source_index < len(source_entries):
            return False
        moving = self._entry(source_path)
        if target_path is None or position == QAbstractItemView.DropIndicatorPosition.OnViewport:
            destination_entries, destination_index = self._operation_files, len(self._operation_files)
        else:
            target = self._entry(target_path)
            if (
                position == QAbstractItemView.DropIndicatorPosition.OnItem
                and self._group(target)
            ):
                if len(target_path) + self._group_depth(moving) >= MOD_CONFIG_MAX_GROUP_DEPTH:
                    self._safe_warning(
                        self,
                        tr("ui.mod_editor_add_group"),
                        tr("ui.mod_editor_group_depth_limit", limit=MOD_CONFIG_MAX_GROUP_DEPTH - 1),
                    )
                    return False
                destination_entries = next(iter(target.values()))
                if not isinstance(destination_entries, list):
                    return False
                destination_index = len(destination_entries)
            elif position == QAbstractItemView.DropIndicatorPosition.OnItem:
                if self._group(moving) or not self._can_create_group_at(target_path[:-1]):
                    return False
                if (name := self._prompt_group_name()) is None:
                    return False
                target_entries = self._entries(target_path[:-1])
                target_index = target_path[-1]
                moving = source_entries.pop(source_index)
                if source_entries is target_entries and source_index < target_index:
                    target_index -= 1
                target_entry = target_entries.pop(target_index)
                group = {name: [target_entry, moving]}
                target_entries.insert(target_index, group)
                self._invalidate_hashes()
                self._refresh_tree(self._find_entry_path(group))
                return True
            else:
                destination_entries = self._entries(target_path[:-1])
                destination_index = target_path[-1]
                if position != QAbstractItemView.DropIndicatorPosition.AboveItem:
                    destination_index += 1
        moving = source_entries.pop(source_index)
        if source_entries is destination_entries and source_index < destination_index:
            destination_index -= 1
        destination_entries.insert(destination_index, moving)
        self._invalidate_hashes()
        self._refresh_tree(self._find_entry_path(moving) if isinstance(moving, dict) else None)
        return True

    def _rename_group(self) -> None:
        if self._loading or (path := self._path()) is None:
            return
        entry = self._entry(path)
        name = self._group_name.text().strip()
        if self._group(entry) and name:
            old, children = next(iter(entry.items()))
            if name != old:
                if not self._group_name_available(name, entry):
                    self._safe_warning(
                        self,
                        tr("ui.mod_editor_group"),
                        tr("ui.mod_editor_group_name_taken"),
                    )
                    self._group_name.setText(old)
                    return
                entry.clear()
                entry[name] = children
                self._refresh_tree(path)

    def _save_entry(self) -> None:
        if self._loading or (path := self._path()) is None:
            return
        entry = self._entry(path)
        if self._group(entry):
            return
        source_changed = entry.get("source") != self._source.text().strip()
        target_changed = entry.get("target") != self._target.text().strip()
        entry["source"] = self._source.text().strip()
        entry["type"] = str(self._type.currentData() or "overwrite")
        target_key = (path, "target_hash")
        if entry["type"] == "info":
            entry.pop("target", None)
            entry.pop("target_hash", None)
            self._hash_enabled.discard(target_key)
            self._cancel_hash(target_key)
        else:
            entry["target"] = self._target.text().strip()
        self._refresh_tree(path)
        if source_changed and self._source_hash_box.isChecked():
            entry.pop("source_hash", None)
            self._start_hash(path, "source_hash")
        if target_changed and entry["type"] != "info" and self._target_hash_box.isChecked():
            entry.pop("target_hash", None)
            self._start_hash(path, "target_hash")

    def _context(self) -> ModPathContext | None:
        root = self._find_mod_folder()
        game = get_game(str(self.game_combo.currentData() or ""))
        if not root or game is None:
            return None
        config = self._cfg if isinstance(self._cfg, dict) else {}
        game_path = game.get_game_path(config)
        key = game.get_custom_exec_config_key()
        custom = config.get(key, "") if key else ""
        executable = custom if isinstance(custom, str) and os.path.isfile(custom) else resolve_game_executable(game_path, game.executable_type)
        return ModPathContext.create(mod_path=root, game_path=game_path, game_data_path=game.get_data_path(config), user_path=Path.home(), runtime=resolve_execution_runtime(executable))

    @staticmethod
    def _within(path: str, root: Path | None) -> str | None:
        if root is None:
            return None
        try:
            return Path(path).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return None

    def _browse_source(self) -> None:
        root = self._find_mod_folder() or self._last_browse_dir
        selected, _ = get_open_file_name(self, tr("ui.select_file"), root, "All Files (*)")
        if not selected:
            return
        self._last_browse_dir = os.path.dirname(selected)
        relative = self._within(selected, Path(root))
        self._source.setText(f"${{mod_path}}/{relative}" if relative is not None else portable_user_path(selected))
        self._save_entry()

    def _browse_target(self) -> None:
        context = self._context()
        selected = get_existing_directory(self, tr("dialogs.select_custom_target_folder"), str(context.game_path) if context and context.game_path else self._last_browse_dir)
        if not selected:
            return
        self._last_browse_dir = selected
        roots = (
            ("${game_path}", context.game_path if context else None),
            ("${game_data_path}", context.game_data_path if context else None),
            ("${user_path}", context.user_path if context else None),
        )
        for placeholder, root in roots:
            if (relative := self._within(selected, root)) is not None:
                self._target.setText(
                    f"{placeholder}/{relative}/" if relative else f"{placeholder}/"
                )
                self._save_entry()
                return
        self._target.setText(f"{portable_user_path(selected).rstrip('/')}/")
        self._save_entry()
        self._safe_warning(
            self,
            tr("dialogs.custom_target_warning_title"),
            tr("dialogs.custom_target_warning"),
        )

    def _hash_target(self, path: tuple[int, ...], field: str):
        context = self._context()
        if context is None:
            return None, tr("ui.mod_editor_hash_context_unavailable")
        entry = self._entry(path)
        index = next((number for number, (_groups, leaf) in enumerate(iter_mod_config_leaves(self._operation_files), 1) if leaf is entry), None)
        try:
            plan = build_mod_operation_plan(self._config("editor_hash"), context)
        except ValueError as error:
            return None, str(error)
        operation = next((item for item in plan.operations if item.index == index), None)
        if operation is None:
            return None, tr("ui.mod_editor_hash_context_unavailable")
        return operation.source if field == "source_hash" else operation.target, None

    def _cancel_hash(self, key) -> None:
        self._hash_pending.pop(key, None)
        self._hash_errors.pop(key, None)
        self._hash_generations[key] = self._hash_generations.get(key, 0) + 1

    def _toggle_hash(self, field: str, enabled: bool) -> None:
        if self._loading or (path := self._path()) is None:
            return
        entry = self._entry(path)
        if self._group(entry) or (field == "target_hash" and entry.get("type") == "info"):
            return
        key = (path, field)
        self._hash_errors.pop(key, None)
        if not enabled:
            self._hash_enabled.discard(key)
            self._cancel_hash(key)
            entry.pop(field, None)
            self._refresh_tree(path)
            return
        self._hash_enabled.add(key)
        entry.pop(field, None)
        self._start_hash(path, field)

    def _start_hash(self, path: tuple[int, ...], field: str) -> None:
        key = (path, field)
        self._hash_enabled.add(key)
        target, error = self._hash_target(path, field)
        generation = self._hash_generations.get(key, 0) + 1
        self._hash_generations[key] = generation
        if target is None:
            self._hash_pending.pop(key, None)
            self._hash_errors[key] = error or tr("ui.mod_editor_hash_context_unavailable")
            self._refresh_tree(path)
            return
        self._hash_pending[key] = generation
        self._refresh_tree(path)
        thread = _OperationHashThread(path, field, generation, target, self)
        self._hash_threads.add(thread)
        thread.result_ready.connect(self._hash_ready)
        thread.finished.connect(self._retire_hash)
        thread.start()

    def _retire_hash(self) -> None:
        thread = self.sender()
        self._hash_threads.discard(thread)
        retire_qthread(thread)

    def _hash_ready(self, path, field, generation, value, error) -> None:
        key = (path, field)
        if self._hash_pending.get(key) != generation:
            return
        self._hash_pending.pop(key, None)
        try:
            entry = self._entry(path)
        except (IndexError, ValueError):
            return
        if error:
            self._hash_errors[key] = error
            entry.pop(field, None)
        else:
            self._hash_errors.pop(key, None)
            entry[field] = value
        if self._path() == path:
            self._refresh_tree(path)
        else:
            self._validate()

    def _config(self, mod_id: str | None = None) -> dict[str, object]:
        tags = [
            name
            for name, box in (
                ("textedit", self.tag_textedit),
                ("customization", self.tag_customization),
                ("gameplay", self.tag_gameplay),
                ("other", self.tag_other),
            )
            if box.isChecked()
        ]
        existing_tags = self.mod_data.get("tags")
        if isinstance(existing_tags, list) and CYOP_AFOM_TAG in existing_tags:
            tags.append(CYOP_AFOM_TAG)
        config: dict[str, object] = {
            "config_version": MOD_CONFIG_VERSION,
            "id": mod_id or self.mod_id or "editor_hash",
            "name": self.name_edit.text().strip() or "Editor hash",
            "version": self.version_edit.text().strip() or "0",
            "authors": _parse_authors(self.authors_edit.text()),
            "game": str(self.game_combo.currentData() or "deltarune"),
            "files": self._operation_files,
        }
        if self._custom_placeholders:
            config["placeholders"] = deepcopy(self._custom_placeholders)
        for field, value in (("description", self.description_edit.text().strip()), ("homepage", self.homepage_edit.text().strip()), ("game_version", self.game_version_edit.text().strip()), ("tags", tags)):
            if value:
                config[field] = value
            else:
                config.pop(field, None)
        icon = self.icon_edit.text().strip()
        if icon:
            config["icon"] = (
                icon
                if icon.startswith(("${", "http://", "https://"))
                else f"${{mod_path}}/{os.path.basename(icon)}"
            )
        else:
            config.pop("icon", None)
        for field in ("dependencies", "conflicts"):
            if values := self._relation_values(field):
                config[field] = values
            else:
                config.pop(field, None)
        return config

    @staticmethod
    def _issue_for(
        issues: tuple[ConfigValidationIssue, ...], path: str
    ) -> ConfigValidationIssue | None:
        return next(
            (
                issue
                for issue in issues
                if issue.path == path
                or issue.path.startswith(f"{path}.")
                or issue.path.startswith(f"{path}[")
            ),
            None,
        )

    def _field_label(self, path: str) -> str:
        for suffix, key in (
            (".source_hash", "ui.mod_editor_source_hash"),
            (".target_hash", "ui.mod_editor_target_hash"),
            (".source", "ui.mod_editor_source"),
            (".target", "ui.mod_editor_target"),
            (".type", "ui.mod_editor_type"),
        ):
            if path.endswith(suffix):
                return tr(key)
        if path.startswith("authors"):
            return tr("ui.mod_editor_authors")
        if path.startswith("dependencies"):
            return tr("ui.mod_editor_dependencies")
        if path.startswith("conflicts"):
            return tr("ui.mod_editor_conflicts")
        if path.startswith("files"):
            return tr("ui.mod_editor_operation")
        return tr("ui.mod_editor_validation_configuration")

    def _issue_message(self, issue: ConfigValidationIssue) -> str:
        operation_type = ""
        if (path := self._path()) is not None:
            try:
                entry = self._entry(path)
            except (IndexError, ValueError):
                entry = {}
            if not self._group(entry):
                operation_type = str(entry.get("type", ""))
        if issue.code == "target_kind":
            if "directory" in issue.message:
                return tr(
                    "ui.mod_editor_validation_target_directory",
                    operation=(
                        self._operation_type_label(operation_type)
                        if operation_type
                        else tr("ui.mod_editor_operation")
                    ),
                )
            return tr("ui.mod_editor_validation_target_unsupported")
        if issue.code == "source_kind":
            return tr("ui.mod_editor_validation_source_file")
        if issue.code == "missing_field":
            if "target" in issue.message:
                return tr("ui.mod_editor_validation_target_required")
            if "source" in issue.message:
                return tr("ui.mod_editor_validation_source_required")
        if issue.code == "forbidden_field":
            return tr("ui.mod_editor_validation_not_used")
        return tr("ui.mod_editor_validation_invalid")

    def _format_issue(self, issue: ConfigValidationIssue) -> str:
        return tr(
            "ui.mod_editor_validation_format",
            field=self._field_label(issue.path),
            message=self._issue_message(issue),
        )

    def _set_field_issue(
        self, widget: QWidget, issue: ConfigValidationIssue | None
    ) -> None:
        message = self._format_issue(issue) if issue else ""
        widget.setStyleSheet("border: 1px solid #d9534f;" if message else "")
        widget.setToolTip(message)

    @staticmethod
    def _entry_config_path(path: tuple[int, ...]) -> str:
        return "files" + "".join(f"[{index}]" for index in path)

    def _apply_validation(self, issues: tuple[ConfigValidationIssue, ...]) -> None:
        fields = (
            (self.name_edit, "name"),
            (self.authors_edit, "authors"),
            (self.description_edit, "description"),
            (self.homepage_edit, "homepage"),
            (self.icon_edit, "icon"),
            (self.version_edit, "version"),
            (self.game_version_edit, "game_version"),
        )
        for widget, path in fields:
            self._set_field_issue(widget, self._issue_for(issues, path))
        for field, tree in self._relation_trees.items():
            issue = self._issue_for(issues, field)
            self._set_field_issue(tree, issue)
            item = tree.currentItem()
            item_index = tree.indexOfTopLevelItem(item) if item else -1
            item_issue = (
                self._issue_for(issues, f"{field}[{item_index}]")
                if item_index >= 0
                else None
            )
            self._set_field_issue(self._relation_id_edits[field], item_issue)
            self._set_field_issue(self._relation_mode_combos[field], item_issue)
        selected = self._path()
        base = self._entry_config_path(selected) if selected is not None else "files"
        entry = self._entry(selected) if selected is not None else {}
        group = selected is not None and self._group(entry)
        self._set_field_issue(self._group_name, self._issue_for(issues, base) if group else None)
        for widget, field in (
            (self._type, "type"),
            (self._source, "source"),
            (self._source_hash, "source_hash"),
            (self._target, "target"),
            (self._target_hash, "target_hash"),
        ):
            self._set_field_issue(widget, self._issue_for(issues, f"{base}.{field}") if not group else None)

        def paint(item) -> None:
            raw = item.data(0, Qt.ItemDataRole.UserRole)
            path = tuple(int(part) for part in raw.split("/")) if raw else ()
            issue = self._issue_for(issues, self._entry_config_path(path))
            item.setForeground(0, QBrush(QColor("#d9534f")) if issue else QBrush())
            item.setToolTip(0, self._format_issue(issue) if issue else "")
            for index in range(item.childCount()):
                paint(item.child(index))

        for index in range(self._tree.topLevelItemCount()):
            paint(self._tree.topLevelItem(index))

    def _validate(self) -> bool:
        issues = validate_mod_config(self._config())
        selected = self._path()
        selected_issue = self._issue_for(
            issues, self._entry_config_path(selected) if selected is not None else "files"
        )
        issue = selected_issue or (issues[0] if issues else None)
        hash_message = next(iter(self._hash_errors.values()), "")
        if self._hash_pending and not hash_message:
            hash_message = tr("ui.mod_editor_hash_pending")
        self._validation.setText(self._format_issue(issue) if issue else hash_message)
        self._validation.setVisible(bool(issues) or bool(hash_message))
        self._apply_validation(issues)
        valid = not any(issue.severity == "error" for issue in issues) and not self._hash_pending and not self._hash_errors
        if hasattr(self, "_save_button"):
            self._save_button.setEnabled(valid)
        return valid

    def _build_actions(self, parent) -> None:
        row = QHBoxLayout()
        if not self.is_creating:
            for key, callback in (("ui.delete_mod", self._delete), ("ui.export_mod", self._export), ("ui.open_mod_folder", self._open_folder)):
                button = self.localize_text(QPushButton(self), key)
                button.clicked.connect(callback)
                row.addWidget(button)
            versions = self.localize_text(QPushButton(self), "mod_versions.switch_version_button")
            versions.clicked.connect(self._open_versions)
            row.addWidget(versions)
        row.addStretch()
        cancel = self.localize_text(QPushButton(self), "ui.cancel_button")
        cancel.clicked.connect(self._cancel)
        row.addWidget(cancel)
        self._save_button = QPushButton(tr("ui.finish_creation") if self.is_creating else tr("ui.save_changes"), self)
        self._save_button.clicked.connect(self._save)
        row.addWidget(self._save_button)
        parent.addLayout(row)

    def _populate(self) -> None:
        data = self.mod_data
        self.name_edit.setText(str(data.get("name", "")))
        authors = data.get("authors")
        self.authors_edit.setText(", ".join(map(str, authors)) if isinstance(authors, list) else "")
        for field, widget in (("description", self.description_edit), ("homepage", self.homepage_edit), ("version", self.version_edit), ("game_version", self.game_version_edit)):
            widget.setText(str(data.get(field, "")))
        self.icon_edit.setText(str(data.get("icon", "")).removeprefix("${mod_path}/"))
        raw_tags = data.get("tags")
        tags: list[object] = raw_tags if isinstance(raw_tags, list) else []
        for name, box in (("textedit", self.tag_textedit), ("customization", self.tag_customization), ("gameplay", self.tag_gameplay), ("other", self.tag_other)):
            box.setChecked(name in tags)
        if self.is_creating and not tags:
            self.tag_other.setChecked(True)
        wanted = data.get("game") or getattr(getattr(self._app_state, "game_mode", None), "game_id", "")
        for index in range(self.game_combo.count()):
            if self.game_combo.itemData(index) == wanted:
                self.game_combo.setCurrentIndex(index)
                break
        for field in ("dependencies", "conflicts"):
            self._populate_relations(field, data.get(field))
        self._refresh_custom_placeholders()
        self._refresh_tree()

    def _browse_icon(self) -> None:
        path, _ = get_open_file_name(self, tr("ui.select_icon_file"), self._last_browse_dir, get_file_filter("image_files"))
        if path:
            self._last_browse_dir = os.path.dirname(path)
            self.icon_edit.setText(path)

    def _load_icon_preview(self, path: str) -> None:
        self._icon_preview_timer.stop()
        path = path.strip()
        custom_placeholders = (
            self._custom_placeholders
            if isinstance(self._custom_placeholders, dict)
            else {}
        )
        candidate = mod_local_relative_path(
            path,
            custom_placeholders,
        ) or path
        if not candidate.startswith(("http://", "https://")) and not os.path.isabs(candidate):
            candidate = os.path.join(self._find_mod_folder() or "", candidate)
        scale = get_ui_scale_factor(getattr(self._app_state, "local_config", None))
        side = round(64 * scale)
        self.icon_preview.setFixedSize(side, side)
        load_mod_icon_universal(
            self.icon_preview,
            SimpleNamespace(icon=candidate),
            size=side,
            border_radius=round(self._radius(64, 64) * scale),
            border_width=round(2 * scale),
            border_color=self._color("border", "#039d5b"),
        )

    def _valid_for_save(self) -> bool:
        if not self.name_edit.text().strip():
            self._safe_warning(self, tr("errors.error"), tr("dialogs.mod_name_empty"))
            return False
        homepage = self.homepage_edit.text().strip()
        try:
            parsed = urlparse(homepage) if homepage else None
        except ValueError:
            self._safe_warning(self, tr("errors.error"), tr("dialogs.invalid_homepage"))
            return False
        if parsed and (parsed.scheme not in {"http", "https"} or not parsed.netloc):
            self._safe_warning(self, tr("errors.error"), tr("dialogs.invalid_homepage"))
            return False
        return self._validate()

    def _copy_icon(self, root: str) -> str | None:
        source = self.icon_edit.text().strip()
        if not source:
            return None
        if source.startswith("${mod_path}/"):
            return source.removeprefix("${mod_path}/")
        if not os.path.isfile(source):
            source = os.path.join(self._find_mod_folder() or "", source)
        if not os.path.isfile(source):
            return None
        filename = os.path.basename(source)
        target = os.path.join(root, filename)
        if os.path.abspath(source) != os.path.abspath(target):
            shutil.copy2(source, target)
        return filename

    def _save(self) -> None:
        if not self._valid_for_save():
            return
        try:
            if self.is_creating:
                mod_id = f"local_{uuid.uuid4().hex[:12]}"
                root = os.path.join(self.parent_app.app_state.mods_dir, get_unique_mod_dir(self.parent_app.app_state.mods_dir, self.name_edit.text().strip()))
                os.makedirs(root)
            else:
                mod_id, root = self.mod_id, self._find_mod_folder()
                if not mod_id or not root:
                    raise FileNotFoundError("mod folder")
            config = self._config(mod_id)
            if icon := self._copy_icon(root):
                config["icon"] = f"${{mod_path}}/{icon}"
            write_mod_config(os.path.join(root, "mod_config.json"), config)
        except Exception as error:
            self._safe_critical(self, tr("errors.update_error"), tr("errors.local_mod_update_failed", error=format_filesystem_error(error)))
            return
        self._refresh_library()
        self.accept()
        self._safe_information(self, tr("dialogs.success"), tr("dialogs.local_mod_created_message" if self.is_creating else "dialogs.local_mod_updated_message", mod_name=self.name_edit.text().strip()))

    def _find_mod_folder(self) -> str | None:
        path = self.mod_data.get("folder_path")
        if isinstance(path, str) and os.path.isdir(path):
            return path
        service = getattr(self.parent_app, "mod_service", None)
        found = service.get_mod_folder_path(self.mod_id) if service and self.mod_id else None
        return found if isinstance(found, str) and os.path.isdir(found) else None

    def _refresh_library(self) -> None:
        service = getattr(self.parent_app, "mod_service", None)
        if service:
            service.invalidate_mods_cache()
            service.load_local_mods()
            service.mod_list_updated.emit()
        if display := getattr(self.parent_app, "library_display", None):
            display.update_display()

    def _relocalize_form_labels(self) -> None:
        width = max(
            label.fontMetrics().horizontalAdvance(tr(key))
            for key, label in self._form_labels.items()
        )
        for key, label in self._form_labels.items():
            label.setText(tr(key))
            label.setFixedWidth(width)

    def closeEvent(self, a0) -> None:
        event = cast(QCloseEvent, a0)
        for thread in list(self._hash_threads):
            retire_qthread(thread)
        self._hash_threads.clear()
        super().closeEvent(event)

    def _cancel(self) -> None:
        self.reject()

    def reject(self) -> None:
        if self._safe_question(
            self, tr("dialogs.cancel_changes"), tr("dialogs.unsaved_changes_lost")
        ) == QMessageBox.StandardButton.Yes:
            super().reject()

    def _delete(self) -> None:
        root = self._find_mod_folder()
        if not root or self._safe_question(self, tr("dialogs.are_you_sure"), tr("dialogs.local_mod_deletion_confirmation")) != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(root)
        except OSError as error:
            self._safe_critical(self, tr("errors.deletion_error"), format_filesystem_error(error, path=root))
            return
        self._refresh_library()
        self.accept()

    def _export(self) -> None:
        root = self._find_mod_folder()
        if not root:
            return
        path, _ = get_save_file_name(self, tr("ui.select_export_location"), f"{self.name_edit.text().strip() or 'mod'}.zip", "ZIP Archives (*.zip);;All Files (*)")
        if not path:
            return
        try:
            if Path(path).resolve().is_relative_to(Path(root).resolve()):
                raise ValueError("Export destination must be outside the mod folder")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for directory, _folders, files in os.walk(root):
                    for filename in files:
                        source = os.path.join(directory, filename)
                        if not os.path.islink(source):
                            archive.write(source, os.path.relpath(source, root))
        except (OSError, ValueError) as error:
            self._safe_critical(self, tr("errors.error"), format_filesystem_error(error, path=path))
            return
        self._safe_information(self, tr("dialogs.success"), tr("status.mod_exported_success"))

    def _open_folder(self) -> None:
        if root := self._find_mod_folder():
            open_path_native(root)

    def _open_versions(self) -> None:
        if root := self._find_mod_folder():
            from ui.dialogs.mod.versions_dialog import ModVersionsDialog
            ModVersionsDialog(root, self.mod_data, self._app_state, self).exec()

    @staticmethod
    def _safe_information(*args) -> None:
        try:
            QMessageBox.information(*args)
        except RuntimeError:
            logger.exception("Could not show information dialog")

    @staticmethod
    def _safe_warning(*args) -> None:
        try:
            QMessageBox.warning(*args)
        except RuntimeError:
            logger.exception("Could not show warning dialog")

    @staticmethod
    def _safe_critical(*args) -> None:
        try:
            QMessageBox.critical(*args)
        except RuntimeError:
            logger.exception("Could not show critical dialog")

    @staticmethod
    def _safe_question(*args) -> QMessageBox.StandardButton:
        try:
            return QMessageBox.question(*args)
        except RuntimeError:
            logger.exception("Could not show question dialog")
            return QMessageBox.StandardButton.No

    def relocalize_ui(self) -> None:
        super().relocalize_ui()
        self._save_button.setText(tr("ui.finish_creation" if self.is_creating else "ui.save_changes"))
        for edit in self._relation_id_edits.values():
            edit.setPlaceholderText(tr("ui.mod_editor_relation_mod_id_placeholder"))
        self.setWindowTitle(tr("ui.create_mod") if self.is_creating else tr("ui.edit_mod"))
        self._tabs.setTabText(0, tr("ui.mod_editor_tab_metadata"))
        self._tabs.setTabText(1, tr("ui.mod_editor_tab_compatibility"))
        self._tabs.setTabText(2, tr("ui.mod_editor_tab_files"))
        self._tabs.setTabText(3, tr("ui.mod_editor_tab_placeholders"))
        self._tabs.setTabText(4, tr("ui.mod_editor_tab_help"))
        self._files_hint.setText(tr("ui.mod_editor_files_hint"))
        self._compatibility_hint.setText(tr("ui.mod_editor_compatibility_hint"))
        self._custom_placeholders_hint.setText(
            tr("ui.mod_editor_custom_placeholders_hint")
        )
        self._custom_placeholders_tree.setHeaderLabels(
            [
                tr("ui.mod_editor_placeholder_name"),
                tr("ui.mod_editor_placeholder_path"),
            ]
        )
        self._custom_placeholder_name.setPlaceholderText(
            tr("ui.mod_editor_placeholder_name_placeholder")
        )
        self._custom_placeholder_path.setPlaceholderText(
            tr("ui.mod_editor_placeholder_path_placeholder")
        )
        for index, section in enumerate(_HELP_SECTIONS):
            title = tr(f"ui.mod_editor_help_{section}_title")
            self._help_tabs.setTabText(index, title.replace("&", "&&"))
            body = tr(f"ui.mod_editor_help_{section}_body")
            if section == "placeholders":
                body += self._placeholder_examples_html()
            self._help_sections[section].setHtml(body)
        for field, tree in self._relation_trees.items():
            self._relation_titles[field].setText(tr(f"ui.mod_editor_{field}"))
            tree.setHeaderLabels(
                [tr("ui.mod_editor_relation_mod_id"), tr("ui.mod_editor_relation_order")]
            )
            self._populate_relation_modes(self._relation_mode_combos[field])
            for index in range(tree.topLevelItemCount()):
                item = cast(QTreeWidgetItem, tree.topLevelItem(index))
                _relation_id, mode = self._split_relation(
                    item.data(0, Qt.ItemDataRole.UserRole)
                )
                item.setText(1, tr(self._relation_mode_key(mode)))
        self._operation_tree_title.setText(tr("ui.mod_editor_processing_order"))
        self._relocalize_form_labels()
        self._populate_operation_types()
        path = self._path()
        entry = self._entry(path) if path is not None else None
        self._operation_inspector_title.setText(
            tr("ui.mod_editor_group")
            if entry is not None and self._group(entry)
            else tr("ui.mod_editor_operation")
        )
        self._source_hash_box.setText(tr("ui.mod_editor_include_source_hash"))
        self._target_hash_box.setText(tr("ui.mod_editor_include_target_hash"))
        self._validate()

    def apply_theme(self) -> None:
        self._cfg = getattr(self._app_state, "local_config", {}) or {}
        border = self._color("border", "#039d5b")
        elements = self._color("elements", "#222222")
        main_text = self._color("main_text", "#e8e9eb")
        secondary_text = self._color("secondary_text", "#6de985")
        hover = self._color("hover", "#616b78")
        self._refresh_operation_icons()
        self._populate_operation_types()
        self.set_theme_stylesheet(
            f"QFrame#modEditorFrame {{ border: 2px solid {border}; border-radius: {self._radius()}px; background: {self._color('background', '#282828')}; }} "
            f"QFrame#modEditorOperationPane {{ border: 2px solid {border}; border-radius: {self._radius()}px; background: {elements}; }} "
            f"QLineEdit, QComboBox, QTreeWidget {{ background: {elements}; }} "
            f"QTabWidget::tab-bar {{ alignment: center; }} "
            f"QTabWidget::pane {{ border: 2px solid {border}; border-radius: {self._radius()}px; background: {self._color('background', '#282828')}; top: -2px; }} "
            f"QTabBar::tab {{ background: {elements}; color: {main_text}; border: 2px solid {border}; border-bottom: none; padding: 6px 16px; margin: 0 3px 4px; border-top-left-radius: {self._radius()}px; border-top-right-radius: {self._radius()}px; }} "
            f"QTabBar::tab:selected {{ background: {hover}; border-bottom: 2px solid {self._color('background', '#282828')}; margin-bottom: 0; }} "
            f"QTabBar::tab:hover {{ background: {hover}; }} "
            f"QLabel#modEditorPaneTitle {{ padding: 7px 10px; color: {secondary_text}; font-weight: bold; border-bottom: 1px solid {border}; }} "
            f"QTreeWidget#modEditorOperationTree {{ border: none; outline: none; }} "
            f"QTreeWidget#modEditorOperationTree::item {{ min-height: 28px; padding: 3px 6px; }} "
            f"QTreeWidget#modEditorOperationTree::item:hover, QTreeWidget#modEditorOperationTree::item:selected {{ background: {hover}; color: {main_text}; }} "
            f"QTextBrowser#modEditorHelpText {{ border: 1px solid {border}; border-radius: {self._radius()}px; padding: 10px; background: {elements}; color: {main_text}; }} "
            f"QLabel#modEditorHint {{ color: {secondary_text}; }} QLabel#modEditorValidation {{ color: #d9534f; }}"
        )
        def refresh(item: QTreeWidgetItem) -> None:
            path = self._item_path(item)
            try:
                entry = self._entry(path) if path else None
            except (IndexError, ValueError):
                entry = None
            if entry is not None and self._group(entry):
                if item.foreground(0).color() != QColor("#d9534f"):
                    item.setForeground(0, QBrush(QColor(secondary_text)))
            elif entry is not None:
                item.setIcon(1, self._operation_icon(str(entry.get("type", ""))))
            for index in range(item.childCount()):
                refresh(cast(QTreeWidgetItem, item.child(index)))
        for index in range(self._tree.topLevelItemCount()):
            refresh(cast(QTreeWidgetItem, self._tree.topLevelItem(index)))
        self._relocalize_form_labels()
        self.icon_browse_button.setIcon(colored_icon("folder", self._color("main_text", "#e8e9eb")))
        self._source_browse.setIcon(colored_icon("folder", self._color("main_text", "#e8e9eb")))
        self._target_browse.setIcon(colored_icon("folder", self._color("main_text", "#e8e9eb")))
        self._load_icon_preview(self.icon_edit.text())

    @override
    def showEvent(self, a0) -> None:
        event = cast(QShowEvent, a0)
        super().showEvent(event)
        if screen := self.screen():
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
