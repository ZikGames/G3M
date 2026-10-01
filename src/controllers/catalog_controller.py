"""Combined plugins and themes catalog controller."""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.config import UI_COLORS
from models.catalog_models import CatalogThemeEntry
from models.download_models import SourceKind, TargetKind
from models.plugin_models import PLUGIN_API_VERSION
from services.localization_service import localization_service, tr
from services.plugins.support import is_version_compatible, resolve_plugin_path
from services.settings_themes import theme_archive_path
from ui.common.styling import (
    clear_layout_widgets,
    get_border_radius,
    get_card_button_metrics,
    get_card_layout_scale,
    get_theme_color,
    load_mod_icon_universal,
    show_empty_message_in_layout,
    update_mod_widget_style,
)
from ui.dialogs.plugin_details_dialog import PluginDetailsDialog
from ui.utils.thread_lifetime import ManagedQThread, retire_qthread
from utils.path_utils import get_user_themes_dir
from utils.process_utils import format_filesystem_error

logger = logging.getLogger(__name__)

_TAG_TO_ATTR = {
    "interface": "catalog_tag_interface_checkbox",
    "game_experience": "catalog_tag_game_experience_checkbox",
    "tool": "catalog_tag_tool_checkbox",
    "other": "catalog_tag_other_checkbox",
}


def _resolve_text(value: str) -> str:
    if not value:
        return ""
    translated = localization_service.get_text(value)
    return value if translated == f"[{value}]" else translated


class _CatalogWorker(ManagedQThread):
    loaded = pyqtSignal()

    def __init__(self, catalog_service) -> None:
        super().__init__()
        self.catalog_service = catalog_service

    def run(self) -> None:
        try:
            self.catalog_service.refresh_catalog()
        except Exception:
            logger.exception(
                "CatalogController: catalog refresh failed in _CatalogWorker"
            )
        finally:
            self.loaded.emit()


class CatalogController:
    """Owns the catalog tab, filters, and plugin and theme actions."""

    def __init__(
        self,
        app_state,
        feedback_service,
        downloads_manager,
        catalog_service=None,
        plugin_state_service=None,
        plugin_runtime_service=None,
        plugin_install_service=None,
        app_window=None,
        plugin_catalog_service=None,
    ) -> None:
        self.app_state = app_state
        self.feedback_service = feedback_service
        self.downloads_manager = downloads_manager
        self.catalog_service = catalog_service or plugin_catalog_service
        self.plugin_catalog_service = self.catalog_service
        self.plugin_state_service = plugin_state_service
        self.plugin_runtime_service = plugin_runtime_service
        self.plugin_install_service = plugin_install_service
        self.app = app_window
        self._loaded = False
        self._filtering = False
        self._catalog_worker: _CatalogWorker | None = None
        self._plugin_tab_ids: list[str] = []
        self._plugin_tab_signature: tuple[tuple[str, str], ...] = ()
        self._download_buttons: dict[str, QPushButton] = {}
        self.downloads_manager.record_updated.connect(self._on_download_record_updated)
        self.downloads_manager.record_removed.connect(self._on_download_record_removed)

    def _showing_themes(self) -> bool:
        tabs = getattr(self.app, "catalog_type_tabs", None)
        return isinstance(tabs, QWidget) and tabs.currentIndex() == 1

    def _plugin_tag_checkboxes(self) -> dict:
        return {
            tag: checkbox
            for tag, attr in _TAG_TO_ATTR.items()
            if (checkbox := getattr(self.app, attr, getattr(self.app, attr.replace("catalog_", "plugins_"), None))) is not None
        }

    def restore_filter_state(self) -> None:
        if not hasattr(self.app, "catalog_installed_only_checkbox") and not hasattr(self.app, "plugins_installed_only_checkbox"):
            return
        installed_checkbox = getattr(self.app, "catalog_installed_only_checkbox", getattr(self.app, "plugins_installed_only_checkbox", None))
        themes = self._showing_themes()
        filters = self.plugin_state_service.get_filters(themes=True) if themes else self.plugin_state_service.get_filters()
        plugin_checkboxes = self._plugin_tag_checkboxes()
        theme_checkboxes = getattr(self.app, "catalog_theme_tag_checkboxes", {})
        theme_widget = getattr(self.app, "catalog_theme_filters_widget", None)
        self._filtering = True
        try:
            installed_checkbox.setChecked(
                filters["installed_only"]
            )
            for checkbox in plugin_checkboxes.values():
                checkbox.setVisible(not themes)
            if theme_widget is not None:
                theme_widget.setVisible(themes)
            for tag, checkbox in (theme_checkboxes if themes else plugin_checkboxes).items():
                checkbox.setText(tr(f"catalog.theme_tag_{tag}" if themes else f"catalog.tag_{tag}"))
                checkbox.setChecked(tag in filters["tags"])
        finally:
            self._filtering = False

    def _safe_show_message(self, level: str, title: str, message: str = "") -> None:
        try:
            self.feedback_service.show_message(level, title, message)
        except Exception:
            logger.exception("CatalogController: failed to show feedback message")

    def on_tab_changed(self, index: int) -> None:
        if not hasattr(self.app, "settings_tab_widget"):
            return
        if (
            hasattr(self.app, "catalog_tab")
            and self.app.settings_tab_widget.currentWidget() is self.app.catalog_tab
        ):
            self.ensure_loaded()

    def ensure_loaded(self, force_refresh: bool = False) -> None:
        if (
            self._loaded
            and not force_refresh
            and self.catalog_service.is_loaded()
        ):
            return
        self.plugin_runtime_service.scan_installed_plugins(resolve_catalog=False)
        self._loaded = True
        self.refresh_main_tabs(force_rebuild=force_refresh)
        self.render()
        if force_refresh or self._catalog_worker is None:
            self._start_catalog_load()

    def on_filters_changed(self) -> None:
        if self._filtering:
            return
        installed_checkbox = getattr(self.app, "catalog_installed_only_checkbox", getattr(self.app, "plugins_installed_only_checkbox", None))
        themes = self._showing_themes()
        checkboxes = self.app.catalog_theme_tag_checkboxes if themes else self._plugin_tag_checkboxes()
        self.plugin_state_service.set_filters(
            installed_only=bool(installed_checkbox.isChecked()),
            tags=[tag for tag, checkbox in checkboxes.items() if checkbox.isChecked()],
            themes=themes,
        )
        if self._loaded:
            self.render()

    def on_catalog_type_changed(self, _index: int = 0) -> None:
        self.restore_filter_state()
        if self._loaded:
            self.render()

    def render(self) -> None:
        catalog_layout = getattr(self.app, "catalog_plugins_layout", None)
        plugin_layout = catalog_layout if isinstance(catalog_layout, QLayout) else getattr(self.app, "plugins_layout", None)
        theme_candidate = getattr(self.app, "catalog_themes_layout", None)
        theme_layout = theme_candidate if isinstance(theme_candidate, QLayout) else None
        if plugin_layout is None:
            return
        themes_view = self._showing_themes()
        layout = theme_layout if themes_view and theme_layout is not None else plugin_layout
        self._apply_list_style()
        self._download_buttons.clear()
        clear_layout_widgets(layout)
        if themes_view:
            self._render_themes(layout)
            return
        installed = {
            plugin.plugin_id: plugin
            for plugin in self.plugin_runtime_service.list_installed_plugins()
        }
        catalog_entries = self.catalog_service.list_entries(load_if_needed=False)
        filters = self.plugin_state_service.get_filters()
        tag_filter = set(filters["tags"])
        items: list[QWidget] = []

        for plugin in installed.values():
            if tag_filter and not (
                tag_filter & set(plugin.manifest.tags if plugin.manifest else [])
            ):
                continue
            items.append(self._build_installed_card(plugin))
        if not filters["installed_only"]:
            for entry in catalog_entries:
                if entry.id in installed:
                    continue
                if tag_filter and not (tag_filter & set(entry.tags)):
                    continue
                items.append(self._build_catalog_card(entry))
        if not items:
            show_empty_message_in_layout(
                layout,
                tr("catalog.empty"),
                self.app_state.local_config,
                font_size=15,
            )
            return
        for widget in items:
            layout.insertWidget(
                layout.count() - 1, widget
            )

    def _render_themes(self, layout) -> None:
        filters = self.plugin_state_service.get_filters(themes=True)
        tag_filter = set(filters["tags"])
        installed_dir = get_user_themes_dir()
        installed = {
            os.path.splitext(name)[0]: os.path.join(installed_dir, name)
            for name in os.listdir(installed_dir) if name.lower().endswith(".zip")
        } if os.path.isdir(installed_dir) else {}
        entries = self.catalog_service.list_themes(load_if_needed=False)
        items = []
        for entry in entries:
            if tag_filter and not tag_filter & set(entry.tags):
                continue
            path = installed.get(entry.id)
            if filters["installed_only"] and not path:
                continue
            items.append(self._build_theme_card(entry, path))
        if not items:
            show_empty_message_in_layout(layout, tr("catalog.themes_empty"), self.app_state.local_config, font_size=15)
            return
        for widget in items:
            layout.insertWidget(layout.count() - 1, widget)

    def _build_theme_card(self, entry: CatalogThemeEntry, installed_path: str | None):
        card, icon_label, body, actions = self._build_card_shell()
        card.setProperty("catalog_theme_id", entry.id)
        if entry.icon:
            load_mod_icon_universal(icon_label, entry, size=icon_label.width())
        body.addWidget(self._card_header(entry.name, entry.version, entry.author))
        description = QLabel(entry.description)
        description.setObjectName("secondaryText")
        description.setWordWrap(True)
        description.setStyleSheet(f"color: {get_theme_color(self.app_state.local_config, 'secondary_text')}; font-size: 12px;")
        body.addWidget(description)
        if installed_path:
            actions.addWidget(self._action_button(tr("catalog.action_apply"), "cardButtonDownload", lambda p=installed_path: self._apply_catalog_theme(p)))
            actions.addWidget(self._action_button(tr("catalog.action_delete"), "cardButtonUninstall", lambda theme_id=entry.id: self._delete_catalog_theme(theme_id)))
        else:
            button = self._action_button(tr("catalog.action_download"), "cardButtonDownload", lambda: self.download_theme(entry), bool(entry.download_link))
            self._download_buttons[f"theme:{entry.id}"] = button
            self._apply_theme_download_button_state(button, entry)
            actions.addWidget(button)
        return card

    def _apply_catalog_theme(self, path: str) -> None:
        self.app.settings_service._install_theme_from_file(path)

    def _delete_catalog_theme(self, theme_id: str) -> None:
        try:
            path = theme_archive_path(get_user_themes_dir(), theme_id)
            if not os.path.exists(path) or not self.feedback_service.ask_question("dialogs.theme_delete_title", tr("dialogs.theme_delete_prompt", theme=theme_id)):
                return
            os.remove(path)
        except (OSError, ValueError) as error:
            logger.warning("CatalogController: theme deletion failed: %s", error)
            self._safe_show_message("error", "dialogs.error", format_filesystem_error(error))
            return
        self._refresh_appearance_themes()
        self.render()

    def _refresh_appearance_themes(self) -> None:
        theme_controller = getattr(self.app, "theme", None)
        if theme_controller is None or not hasattr(theme_controller, "init_theme_list"):
            return
        try:
            theme_controller.init_theme_list()
        except Exception:
            logger.exception("CatalogController: failed to refresh Appearance themes")

    def download_theme(self, entry) -> None:
        self.downloads_manager.enqueue_with_feedback(
            self.feedback_service, display_name=entry.name, source_kind=SourceKind.EXTERNAL_URL,
            target_kind=TargetKind.THEME, source_url=entry.download_link,
            canonical_key=f"theme:{entry.id}:{entry.version}",
            metadata={"theme_id": entry.id, "source": "catalog", "file_name": f"{entry.id}.zip"},
        )

    def _apply_theme_download_button_state(self, button, entry) -> None:
        record = self._get_theme_download_record(entry.id)
        button.setText(self._download_button_text(record))
        button.setEnabled(bool(entry.download_link and not self._is_plugin_download_busy(record)))

    def _get_theme_download_record(self, theme_id: str):
        for record in reversed(list(getattr(self.downloads_manager, "records", []))):
            if getattr(record, "target_kind", None) == TargetKind.THEME and str((record.metadata or {}).get("theme_id", "")) == theme_id:
                return record
        return None

    def relocalize_ui(self) -> None:
        self.restore_filter_state()
        self.refresh_main_tabs()
        if self._loaded:
            self.render()

    def _on_download_record_updated(self, record) -> None:
        target_kind = getattr(record, "target_kind", None)
        if target_kind not in (TargetKind.PLUGIN, TargetKind.THEME):
            return
        if target_kind == TargetKind.THEME:
            theme_id = str((record.metadata or {}).get("theme_id", "")).strip()
            if theme_id and getattr(record, "effective_status_key", "") in {"downloading", "installing", "ready"}:
                button = self._download_buttons.get(f"theme:{theme_id}")
                entry = self.catalog_service.get_theme(theme_id, load_if_needed=False)
                if button and entry:
                    self._apply_theme_download_button_state(button, entry)
                return
            if theme_id and getattr(record, "effective_status_key", "") == "installed":
                self._refresh_appearance_themes()
            if self._loaded:
                self.render()
            return
        plugin_id = (
            str((record.metadata or {}).get("plugin_id", "")).strip()
            if getattr(record, "metadata", None)
            else ""
        )
        effective_status = getattr(record, "effective_status_key", "")
        if plugin_id and effective_status in {"downloading", "installing", "ready"}:
            self._refresh_download_button_state(plugin_id)
            return
        self.plugin_runtime_service.scan_installed_plugins(
            resolve_catalog=self.catalog_service.is_loaded()
        )
        self.refresh_main_tabs(force_rebuild=True)
        if self._loaded:
            self.render()

    def _on_download_record_removed(self, record) -> None:
        if getattr(record, "target_kind", None) == TargetKind.THEME:
            if self._loaded:
                self.render()
            return
        if getattr(record, "target_kind", None) != TargetKind.PLUGIN:
            return
        self.plugin_runtime_service.scan_installed_plugins(
            resolve_catalog=self.catalog_service.is_loaded()
        )
        self.refresh_main_tabs(force_rebuild=True)

    def handle_theme_refresh(self) -> None:
        if self._loaded:
            self.render()

    def _build_card_shell(self) -> tuple[QFrame, QLabel, QVBoxLayout, QVBoxLayout]:
        parent = next((candidate for candidate in (getattr(self.app, "catalog_widget", None), getattr(self.app, "plugins_widget", None), getattr(self.app, "catalog_container", None), self.app) if isinstance(candidate, QWidget)), None)
        card = QFrame(parent)
        card.setObjectName("pluginCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        icon_label = QLabel(card)
        icon_label.setObjectName("modIcon")
        icon_label.setFixedSize(80, 80)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        body = QVBoxLayout()
        body.setSpacing(2)
        body.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(body, 1)
        actions = QVBoxLayout()
        actions.setSpacing(8)
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(actions, 0)
        card.main_layout = layout
        card.icon_label = icon_label
        update_mod_widget_style(card, "pluginCard", self.app)
        button_width, button_height, button_font_size = get_card_button_metrics(
            self.app_state.local_config
        )
        border = get_theme_color(self.app_state.local_config, "border")
        button = get_theme_color(self.app_state.local_config, "elements")
        hover = get_theme_color(self.app_state.local_config, "hover")
        text = get_theme_color(self.app_state.local_config, "main_text")
        success = get_theme_color(
            self.app_state.local_config, "success", UI_COLORS["status_success"]
        )
        warning = get_theme_color(
            self.app_state.local_config, "warning", UI_COLORS["status_warning"]
        )
        disabled_bg = get_theme_color(self.app_state.local_config, "disabled_bg")
        disabled_text = get_theme_color(self.app_state.local_config, "disabled_text")
        disabled_border = get_theme_color(
            self.app_state.local_config, "disabled_border"
        )
        radius = get_border_radius(self.app_state.local_config)
        card.setStyleSheet(
            card.styleSheet()
            + f"""
QPushButton#cardButton {{
    background-color: {button};
    color: {text};
    border: 2px solid {border};
    border-radius: {radius}px;
    min-width: {button_width}px;
    min-height: {button_height}px;
    font-size: {button_font_size}px;
}}
QPushButton#cardButton:hover {{
    background-color: {hover};
}}
QPushButton#cardButtonDownload {{
    background-color: {success};
    color: {text};
    border: 2px solid {border};
    border-radius: {radius}px;
    min-width: {button_width}px;
    min-height: {button_height}px;
    font-size: {button_font_size}px;
}}
QPushButton#cardButtonDownload:hover {{
    background-color: {hover};
}}
QPushButton#cardButtonUninstall {{
    background-color: {warning};
}}
QPushButton#cardButtonUninstall:hover {{
    background-color: {hover};
}}
QPushButton#cardButtonDownload:disabled,
QPushButton#cardButton:disabled,
QPushButton#cardButtonUninstall:disabled {{
    background-color: {disabled_bg};
    color: {disabled_text};
    border-color: {disabled_border};
}}
"""
        )
        scale = get_card_layout_scale(self.app_state.local_config)
        margin = max(8, round(10 * scale))
        spacing = max(10, round(15 * scale))
        icon_size = max(64, round(80 * scale))
        card_height = max(120, round(120 * scale))

        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(spacing)
        icon_label.setFixedSize(icon_size, icon_size)
        card.setMinimumHeight(card_height)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        icon_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        card.mouseDoubleClickEvent = lambda event, widget=card: (
            self.show_plugin_details(widget.property("plugin_id"))
            if event.button() == Qt.MouseButton.LeftButton
            and widget.property("plugin_id")
            else None
        )
        return card, icon_label, body, actions

    @staticmethod
    def _entry_api_compatible(entry) -> bool:
        return bool(
            entry and is_version_compatible(PLUGIN_API_VERSION, entry.api_version)
        )

    def _action_button(
        self, text: str, role: str, callback, enabled: bool = True
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(role)
        button.clicked.connect(callback)
        button.setEnabled(enabled)
        return button

    def _card_header(
        self,
        title: str,
        version: str,
        author: str,
        badge: str = "",
        badge_tooltip: str = "",
        badge_color: str = "",
    ):
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        text_layout = QVBoxLayout()
        name_label = QLabel(title)
        name_label.setObjectName("primaryText")
        name_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        meta = QLabel(f"{author} | {version}".strip(" |"))
        meta.setObjectName("secondaryText")
        meta_color = get_theme_color(self.app_state.local_config, "secondary_text")
        meta.setStyleSheet(f"font-size: 13px; color: {meta_color};")
        text_layout.addWidget(name_label)
        text_layout.addWidget(meta)
        layout.addLayout(text_layout, 1)
        if badge:
            badge_label = QLabel(badge)
            badge_label.setObjectName("secondaryText")
            badge_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            badge_label.setStyleSheet(f"font-size: 13px; color: {meta_color};")
            if badge_tooltip:
                badge_label.setToolTip(badge_tooltip)
            if badge_color:
                badge_label.setStyleSheet(f"color: {badge_color}; font-size: 13px;")
            layout.addWidget(badge_label)
        return header

    def _set_local_icon(self, icon_label: QLabel, path: str) -> None:
        if path and path.strip():
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                icon_size = icon_label.width()
                icon_label.setPixmap(
                    pixmap.scaled(
                        icon_size,
                        icon_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def _build_catalog_card(self, entry):
        card, icon_label, body, actions = self._build_card_shell()
        card.setProperty("plugin_id", entry.id)
        if entry.icon:
            load_mod_icon_universal(icon_label, entry, size=icon_label.width())
        body.addWidget(self._card_header(entry.name, entry.version, entry.author))
        description = QLabel(entry.description)
        description.setObjectName("secondaryText")
        secondary_color = get_theme_color(self.app_state.local_config, "secondary_text")
        description.setStyleSheet(f"color: {secondary_color}; font-size: 12px;")
        description.setWordWrap(True)
        description.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        body.addWidget(description)

        is_compatible = self._entry_api_compatible(entry)

        if not is_compatible:
            warning = QLabel(
                tr(
                    "plugins.incompatible_api_warning",
                    required_version=entry.api_version,
                    current_version=PLUGIN_API_VERSION,
                )
            )
            warning.setObjectName("warningText")
            warning_color = get_theme_color(self.app_state.local_config, "warning")
            warning.setStyleSheet(
                f"color: {warning_color}; font-size: 11px; font-style: italic;"
            )
            warning.setWordWrap(True)
            warning.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
            )
            body.addWidget(warning)

        download_button = self._action_button(
            tr("catalog.action_download"),
            "cardButtonDownload",
            lambda: self.download_plugin(entry),
            enabled=bool(entry.download_link),
        )
        self._download_buttons[entry.id] = download_button
        self._apply_download_button_state(download_button, entry)
        actions.addWidget(download_button)
        actions.addWidget(
            self._action_button(
                tr("catalog.action_details"),
                "cardButton",
                lambda: self.show_plugin_details(entry.id),
            )
        )
        return card

    def _build_installed_card(self, plugin):
        card, icon_label, body, actions = self._build_card_shell()
        compatible = getattr(plugin, "compatible", True)
        card.setProperty("plugin_id", plugin.plugin_id)
        body.addWidget(
            self._card_header(
                _resolve_text(
                    plugin.manifest.name if plugin.manifest else plugin.plugin_id
                ),
                plugin.manifest.version if plugin.manifest else "",
                plugin.manifest.author if plugin.manifest else "",
                "",
                "",
                "",
            )
        )
        if plugin.manifest and plugin.manifest.icon:
            try:
                self._set_local_icon(
                    icon_label, resolve_plugin_path(plugin.path, plugin.manifest.icon)
                )
            except Exception as exc:
                logger.debug(
                    "CatalogController: failed to load icon for %s: %s",
                    plugin.plugin_id,
                    exc,
                    exc_info=True,
                )
        description = QLabel(
            _resolve_text(
                plugin.manifest.description if plugin.manifest else plugin.error
            )
        )
        description.setObjectName("secondaryText")
        secondary_color = get_theme_color(self.app_state.local_config, "secondary_text")
        description.setStyleSheet(f"color: {secondary_color}; font-size: 12px;")
        description.setWordWrap(True)
        description.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        body.addWidget(description)

        if plugin.is_local:
            badge_text = tr("plugins.badge_local")
            badge_label = QLabel(badge_text)
            badge_label.setObjectName("secondaryText")
            badge_label.setStyleSheet(f"color: {secondary_color}; font-size: 12px;")
            badge_label.setToolTip(tr("plugins.badge_local_tooltip"))
            body.addWidget(badge_label)

        if not compatible:
            warning = QLabel(
                tr(
                    "plugins.incompatible_api_warning",
                    required_version=(
                        plugin.manifest.api_version if plugin.manifest else ""
                    ),
                    current_version=PLUGIN_API_VERSION,
                )
            )
            warning.setObjectName("warningText")
            warning.setWordWrap(True)
            body.addWidget(warning)

        actions.addWidget(
            self._action_button(
                tr(
                    "plugins.action_disable"
                    if plugin.enabled
                    else "plugins.action_enable"
                ),
                "cardButtonUninstall" if plugin.enabled else "cardButtonDownload",
                lambda: self.toggle_plugin(plugin.plugin_id),
            )
        )
        actions.addWidget(
            self._action_button(
                tr("catalog.action_details"),
                "cardButton",
                lambda: self.show_plugin_details(plugin.plugin_id),
            )
        )
        return card

    def download_plugin(self, entry) -> None:
        if not entry.download_link:
            return
        self.downloads_manager.enqueue_with_feedback(
            self.feedback_service,
            display_name=entry.name,
            source_kind=SourceKind.EXTERNAL_URL,
            target_kind=TargetKind.PLUGIN,
            source_url=entry.download_link,
            canonical_key=f"plugin:{entry.id}:{entry.version}",
            metadata={
                "plugin_id": entry.id,
                "catalog_plugin_version": entry.version,
                "source": "catalog",
                "homepage": entry.homepage,
                "file_name": f"{entry.id}.zip",
            },
        )
        self._refresh_download_button_state(entry.id)

    def import_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        imported = False
        for path in paths:
            try:
                self.plugin_install_service.install_path(path, source="manual")
                imported = True
            except Exception as e:
                logger.error(
                    "CatalogController: import failed for %s: %s",
                    path,
                    e,
                    exc_info=True,
                )
                self._safe_show_message(
                    "error", "errors.error", format_filesystem_error(e, path=path)
                )
        if imported:
            self.plugin_runtime_service.scan_installed_plugins(
                resolve_catalog=self.catalog_service.is_loaded()
            )
            self.refresh_main_tabs(force_rebuild=True)
            self.render()

    def toggle_plugin(self, plugin_id: str) -> None:
        plugin = self.plugin_runtime_service.get_plugin(plugin_id)
        if not plugin:
            return
        if plugin.enabled:
            self.plugin_runtime_service.disable_plugin(plugin_id)
        else:
            success, error = self.plugin_runtime_service.enable_plugin(plugin_id)
            if not success:
                self._safe_show_message(
                    "error",
                    "errors.error",
                    error or tr("plugins.enable_failed"),
                )
        self.refresh_main_tabs(force_rebuild=True)
        self.render()

    def show_plugin_details(self, plugin_id: str) -> None:
        plugin = self.plugin_runtime_service.get_plugin(plugin_id)
        entry = (
            None
            if plugin
            else self.catalog_service.get_entry(plugin_id, load_if_needed=False)
        )
        if not plugin and not entry:
            return
        if entry is not None:
            dialog = PluginDetailsDialog(
                None,
                self.plugin_runtime_service,
                self.plugin_state_service,
                self.app_state,
                catalog_entry=entry,
                can_download=bool(entry.download_link),
                parent=self.app,
            )
            dialog.exec()
            if dialog.download_requested:
                self.download_plugin(entry)
            self.render()
            return
        dialog = PluginDetailsDialog(
            plugin,
            self.plugin_runtime_service,
            self.plugin_state_service,
            self.app_state,
            can_update=bool(
                plugin.update_available and plugin.catalog_entry and not plugin.is_local
            ),
            on_update=self.update_plugin,
            parent=self.app,
        )
        dialog.exec()
        if dialog.delete_requested:
            QTimer.singleShot(
                0, lambda plugin_id=plugin_id: self.delete_plugin(plugin_id)
            )
            return
        self.render()

    def update_plugin(self, plugin_id: str) -> None:
        plugin = self.plugin_runtime_service.get_plugin(plugin_id)
        entry = plugin.catalog_entry if plugin else None
        if not entry:
            return
        self.download_plugin(entry)

    def delete_plugin(self, plugin_id: str) -> None:
        plugin = self.plugin_runtime_service.get_plugin(plugin_id)
        try:
            self.plugin_runtime_service.disable_plugin(plugin_id)
            self.plugin_install_service.delete_plugin(plugin_id)
            self.plugin_runtime_service.scan_installed_plugins(
                resolve_catalog=self.catalog_service.is_loaded()
            )
        except Exception as e:
            logger.error(
                "CatalogController: delete failed for %s: %s",
                plugin_id,
                e,
                exc_info=True,
            )
            plugin_path = str(getattr(plugin, "path", "") or "")
            self._safe_show_message(
                "error", "errors.error", format_filesystem_error(e, path=plugin_path)
            )
        self.refresh_main_tabs(force_rebuild=True)
        self.render()

    def _apply_list_style(self) -> None:
        if not hasattr(self.app, "catalog_container"):
            return
        border = get_theme_color(self.app_state.local_config, "border")
        background = get_theme_color(self.app_state.local_config, "background")
        radius = get_border_radius(self.app_state.local_config)
        self.app.catalog_container.setStyleSheet(
            f"QFrame#catalog_settings_container {{"
            f"background-color: {background};"
            f"border: 2px solid {border};"
            f"border-radius: {radius}px;"
            f"}}"
            "QScrollArea { background: transparent; }"
            "QWidget { background: transparent; }"
        )

    def _get_plugin_download_record(self, plugin_id: str):
        for record in reversed(list(getattr(self.downloads_manager, "records", []))):
            if getattr(record, "target_kind", None) != TargetKind.PLUGIN:
                continue
            metadata = getattr(record, "metadata", None) or {}
            if str(metadata.get("plugin_id", "")).strip() == plugin_id:
                return record
        return None

    @staticmethod
    def _is_plugin_download_busy(record) -> bool:
        if not record:
            return False
        return getattr(record, "effective_status_key", "") in {
            "downloading",
            "installing",
        }

    def _download_button_text(self, record) -> str:
        if not record:
            return tr("catalog.action_download")
        effective_status = getattr(record, "effective_status_key", "")
        if effective_status == "downloading":
            progress = max(0, min(100, int(getattr(record, "progress", 0) or 0)))
            return tr("downloads.status_downloading", progress=progress)
        if effective_status == "installing":
            return tr("downloads.status_installing")
        return tr("catalog.action_download")

    def _apply_download_button_state(self, button: QPushButton, entry) -> None:
        record = self._get_plugin_download_record(entry.id)
        button.setText(self._download_button_text(record))
        button.setEnabled(
            bool(entry.download_link and not self._is_plugin_download_busy(record))
        )

    def _refresh_download_button_state(self, plugin_id: str) -> None:
        button = self._download_buttons.get(plugin_id)
        if not button:
            return
        entry = self.catalog_service.get_entry(plugin_id, load_if_needed=False)
        if not entry:
            return
        self._apply_download_button_state(button, entry)

    def _start_catalog_load(self) -> None:
        if self._catalog_worker and self._catalog_worker.isRunning():
            return
        self._catalog_worker = _CatalogWorker(self.catalog_service)
        self._catalog_worker.loaded.connect(self._on_catalog_loaded)
        self._catalog_worker.finished.connect(self._clear_catalog_worker)
        self._catalog_worker.start()

    def _on_catalog_loaded(self) -> None:
        self.plugin_runtime_service.scan_installed_plugins(resolve_catalog=True)
        if self._loaded:
            self.render()

    def _clear_catalog_worker(self) -> None:
        """Clean up the catalog worker safely."""
        if self._catalog_worker is not None:
            try:
                self._catalog_worker.loaded.disconnect()
                self._catalog_worker.finished.disconnect()
            except (TypeError, RuntimeError) as error:
                logger.debug("Best-effort operation failed: %s", error, exc_info=True)

            worker = self._catalog_worker
            self._catalog_worker = None

            if worker.isRunning():
                worker.requestInterruption()
                worker.quit()
            retire_qthread(worker)

    def shutdown(self) -> None:
        """Explicit cleanup method for deterministic shutdown."""
        self._clear_catalog_worker()

    def refresh_main_tabs(self, *, force_rebuild: bool = False) -> None:
        if not hasattr(self.app, "main_tab_widget"):
            return
        tab_widget = self.app.main_tab_widget
        main_view_plugins = [
            plugin
            for plugin in self.plugin_runtime_service.list_installed_plugins()
            if (
                plugin.enabled
                and plugin.manifest
                and "main_view" in plugin.manifest.hooks
            )
        ]
        desired_signature = tuple(
            (plugin.plugin_id, plugin.manifest.name) for plugin in main_view_plugins
        )
        if (
            not force_rebuild
            and desired_signature == self._plugin_tab_signature
            and self._plugin_tabs_are_attached(tab_widget)
        ):
            self._update_plugin_tab_labels(tab_widget, main_view_plugins)
            return
        updates_were_enabled = tab_widget.updatesEnabled()
        tab_widget.setUpdatesEnabled(False)
        try:
            for plugin_id in list(self._plugin_tab_ids):
                widget = getattr(self.app, f"_plugin_tab_{plugin_id}", None)
                if widget is None:
                    continue
                index = tab_widget.indexOf(widget)
                if index >= 0:
                    tab_widget.removeTab(index)
                widget.hide()
                widget.deleteLater()
                delattr(self.app, f"_plugin_tab_{plugin_id}")
            self._plugin_tab_ids.clear()
            self._plugin_tab_signature = ()
            for plugin in main_view_plugins:
                widget = self.plugin_runtime_service.get_main_widget(
                    plugin.plugin_id, tab_widget
                )
                if widget is None:
                    continue
                widget.setWindowFlag(Qt.WindowType.Window, False)
                widget.setParent(tab_widget)
                widget.hide()
                setattr(self.app, f"_plugin_tab_{plugin.plugin_id}", widget)
                tab_widget.addTab(widget, _resolve_text(plugin.manifest.name))
                self._plugin_tab_ids.append(plugin.plugin_id)
            self._plugin_tab_signature = (
                desired_signature
                if len(self._plugin_tab_ids) == len(main_view_plugins)
                else tuple(
                    (plugin.plugin_id, plugin.manifest.name)
                    for plugin in main_view_plugins
                    if plugin.plugin_id in self._plugin_tab_ids
                )
            )
        finally:
            tab_widget.setUpdatesEnabled(updates_were_enabled)
            tab_widget.update()

    def _plugin_tabs_are_attached(self, tab_widget) -> bool:
        for plugin_id in self._plugin_tab_ids:
            widget = getattr(self.app, f"_plugin_tab_{plugin_id}", None)
            if widget is None or tab_widget.indexOf(widget) < 0:
                return False
        return True

    def _update_plugin_tab_labels(self, tab_widget, plugins) -> None:
        labels_by_id = {
            plugin.plugin_id: _resolve_text(plugin.manifest.name)
            for plugin in plugins
            if plugin.manifest
        }
        for plugin_id in self._plugin_tab_ids:
            widget = getattr(self.app, f"_plugin_tab_{plugin_id}", None)
            if widget is None:
                continue
            index = tab_widget.indexOf(widget)
            if index >= 0 and plugin_id in labels_by_id:
                tab_widget.setTabText(index, labels_by_id[plugin_id])
