"""Unit tests for test controller."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QLabel,
    QLayoutItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.dialogs import on_downloads_record_updated
from controllers.plugins_controller import PluginsController
from models.download_models import DownloadRecord, DownloadStatus, TargetKind
from models.plugin_models import CatalogPluginEntry
from services.downloads.manager import DownloadsManager
from services.localization_service import tr


def _make_controller(temp_dir):
    downloads_manager = DownloadsManager(temp_dir, lambda: {})
    downloads_manager.startup()
    plugin_catalog_service = Mock()
    plugin_catalog_service.is_loaded.return_value = False
    plugin_catalog_service.get_entry.return_value = None
    plugin_runtime_service = Mock()
    plugin_runtime_service.get_plugin.return_value = None
    controller = PluginsController(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        downloads_manager=downloads_manager,
        plugin_catalog_service=plugin_catalog_service,
        plugin_state_service=Mock(),
        plugin_runtime_service=plugin_runtime_service,
        plugin_install_service=Mock(),
        app_window=Mock(),
    )
    return controller, downloads_manager, plugin_catalog_service


def test_plugin_download_button_disables_while_busy(qapp, temp_dir):
    """Checks that plugin download button disables while busy."""
    controller, downloads_manager, _catalog = _make_controller(temp_dir)
    entry = CatalogPluginEntry(
        id="sample_plugin",
        name="Sample Plugin",
        description="Desc",
        author="Author",
        version="1.0.0",
        api_version=">=1.0.0",
        download_link="https://example.com/plugin.zip",
    )
    button = QPushButton()

    downloads_manager.store.add(
        DownloadRecord(
            id="rec1",
            display_name="Sample Plugin",
            target_kind=TargetKind.PLUGIN,
            download_status=DownloadStatus.DOWNLOADING,
            progress=37,
            metadata={"plugin_id": "sample_plugin"},
        )
    )

    controller._apply_download_button_state(button, entry)

    assert button.isEnabled() is False
    assert button.text() == tr("downloads.status_downloading", progress=37)


def test_compatible_legacy_plugin_card_keeps_its_display_metadata(qapp, temp_dir):
    from models.plugin_models import InstalledPluginRecord, PluginManifest

    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    assert controller.app is not None
    controller.app.plugins_widget = QWidget()
    image = QImage(1, 1, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(f"{temp_dir}/icon.png")
    plugin = InstalledPluginRecord(
        manifest=PluginManifest(
            config_version=0,
            id="legacy_plugin",
            name="Legacy Plugin",
            description="Still visible",
            author="Author",
            version="1.0.0",
            api_version=">=1.1.0",
            entry="",
            icon="icon.png",
        ),
        path=temp_dir,
        status="broken",
        compatible=True,
    )

    card = controller._build_installed_card(plugin)

    labels = card.findChildren(QLabel)
    assert "Legacy Plugin" in [label.text() for label in labels]
    assert "Still visible" in [label.text() for label in labels]
    assert not card.findChildren(QLabel, "warningText")
    assert any(
        label.pixmap() is not None and not label.pixmap().isNull()
        for label in labels
    )


def test_plugin_download_button_allows_incompatible_api(qapp, temp_dir):
    """Checks that Plugin API mismatch warns but does not disable download."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    entry = CatalogPluginEntry(
        id="future_plugin",
        name="Future Plugin",
        description="Desc",
        author="Author",
        version="1.0.0",
        api_version=">=99.0.0",
        download_link="https://example.com/plugin.zip",
    )
    button = QPushButton()

    controller._apply_download_button_state(button, entry)

    assert button.isEnabled() is True
    assert button.text() == tr("plugins.action_download")


def test_incompatible_plugin_download_starts_without_confirmation(qapp, temp_dir):
    """Checks that API mismatch does not block a plugin download."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    controller.downloads_manager.enqueue_with_feedback = Mock()
    entry = CatalogPluginEntry(
        id="future_plugin",
        name="Future Plugin",
        description="Desc",
        author="Author",
        version="1.0.0",
        api_version=">=99.0.0",
        download_link="https://example.com/plugin.zip",
    )

    controller.download_plugin(entry)

    controller.downloads_manager.enqueue_with_feedback.assert_called_once()


def test_catalog_details_dialog_replaces_homepage_open(qapp, temp_dir, monkeypatch):
    """Checks that catalog Details opens a dialog instead of opening homepage."""
    controller, _downloads_manager, plugin_catalog_service = _make_controller(temp_dir)
    entry = CatalogPluginEntry(
        id="catalog_plugin",
        name="Catalog Plugin",
        description="Desc",
        author="Author",
        version="1.0.0",
        api_version=">=1.0.0",
        homepage="https://example.com",
        download_link="https://example.com/plugin.zip",
    )
    plugin_catalog_service.get_entry.return_value = entry
    created = {}

    class FakeDialog:
        def __init__(self, plugin, runtime_service, state_service, app_state, **kwargs) -> None:
            created["plugin"] = plugin
            created["kwargs"] = kwargs
            self.download_requested = False
            self.delete_requested = False

        def exec(self):
            created["exec"] = True

    monkeypatch.setattr("controllers.plugins_controller.PluginDetailsDialog", FakeDialog)
    controller.render = Mock()

    controller.show_plugin_details("catalog_plugin")

    assert created["plugin"] is None
    assert created["kwargs"]["catalog_entry"] is entry
    assert created["kwargs"]["can_download"] is True
    assert created["exec"] is True
    controller.render.assert_called_once()


def test_plugin_active_download_update_does_not_rerender_tab(qapp, temp_dir):
    """Checks that plugin active download update does not rerender tab."""
    controller, _downloads_manager, plugin_catalog_service = _make_controller(temp_dir)
    controller.render = Mock()
    controller.refresh_main_tabs = Mock()
    controller._loaded = True
    controller._download_buttons["sample_plugin"] = QPushButton()
    plugin_catalog_service.get_entry.return_value = CatalogPluginEntry(
        id="sample_plugin",
        name="Sample Plugin",
        description="Desc",
        author="Author",
        version="1.0.0",
        api_version=">=1.0.0",
        download_link="https://example.com/plugin.zip",
    )
    record = DownloadRecord(
        id="rec1",
        display_name="Sample Plugin",
        target_kind=TargetKind.PLUGIN,
        download_status=DownloadStatus.DOWNLOADING,
        progress=10,
        metadata={"plugin_id": "sample_plugin"},
    )

    controller._on_download_record_updated(record)

    controller.render.assert_not_called()
    controller.refresh_main_tabs.assert_not_called()
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.scan_installed_plugins.assert_not_called()

    controller.render.reset_mock()
    controller.refresh_main_tabs.reset_mock()
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.scan_installed_plugins.reset_mock()

    completed_record = DownloadRecord(
        id="rec2",
        display_name="Sample Plugin",
        target_kind=TargetKind.PLUGIN,
        download_status=DownloadStatus.DOWNLOADED,
        progress=100,
        metadata={"plugin_id": "sample_plugin"},
    )

    controller._on_download_record_removed(completed_record)

    controller.render.assert_not_called()
    controller.refresh_main_tabs.assert_called_once()
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.scan_installed_plugins.assert_called_once()


def test_plugin_installed_record_update_scans_on_main_thread(qapp, temp_dir):
    """Checks that plugin installs are scanned by the controller after UseWorker finishes."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    controller.render = Mock()
    controller.refresh_main_tabs = Mock()
    controller._loaded = True
    record = DownloadRecord(
        id="rec3",
        display_name="Sample Plugin",
        target_kind=TargetKind.PLUGIN,
        download_status=DownloadStatus.DOWNLOADED,
        use_status="ready_to_use",
        file_exists=True,
        ever_installed=True,
        metadata={"plugin_id": "sample_plugin"},
    )

    controller._on_download_record_updated(record)

    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.scan_installed_plugins.assert_called_once()
    controller.refresh_main_tabs.assert_called_once()
    controller.render.assert_called_once()


def test_plugin_list_render_does_not_detach_removed_cards(qapp, temp_dir):
    """Checks that plugin list refresh cannot flash removed cards as windows."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    assert controller.plugin_catalog_service is not None
    controller.plugin_catalog_service.list_entries.return_value = []
    assert controller.plugin_state_service is not None
    controller.plugin_state_service.get_filters.return_value = {
        "installed_only": False,
        "tags": [],
    }
    assert controller.app is not None
    controller.app.plugins_container = QWidget()
    assert controller.app is not None
    controller.app.plugins_widget = QWidget(controller.app.plugins_container)
    assert controller.app is not None
    controller.app.plugins_layout = QVBoxLayout(controller.app.plugins_widget)
    assert controller.app is not None
    controller.app.plugins_layout.addStretch()
    plugin = SimpleNamespace(
        plugin_id="sample_plugin",
        enabled=True,
        is_local=False,
        manifest=SimpleNamespace(
            name="Sample Plugin",
            description="Desc",
            version="1.0.0",
            author="Author",
            icon="",
            tags=[],
        ),
    )
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.list_installed_plugins.return_value = [plugin]

    controller.render()
    assert controller.app is not None
    old_card = cast(QLayoutItem, controller.app.plugins_layout.itemAt(0)).widget()
    assert controller.app is not None
    controller.app.plugins_widget.show()
    assert old_card is not None
    old_card.show()
    qapp.processEvents()

    controller.render()

    assert controller.app is not None
    assert old_card is not None
    assert old_card.parent() is controller.app.plugins_widget
    assert old_card is not None
    assert old_card.isWindow() is False
    assert old_card is not None
    assert old_card.isVisible() is False


def test_plugin_main_view_widget_is_reparented_before_tab_insert(qapp, temp_dir):
    """Checks that plugin main view widgets cannot stay as transient windows."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    assert controller.app is not None
    controller.app.main_tab_widget = QTabWidget()
    plugin = SimpleNamespace(
        plugin_id="sample_plugin",
        enabled=True,
        manifest=SimpleNamespace(
            name="Sample Plugin",
            hooks=["main_view"],
        ),
    )
    widget = QWidget()
    widget.setWindowFlag(Qt.WindowType.Window, True)
    widget.show()
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.list_installed_plugins.return_value = [plugin]
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.get_main_widget.return_value = widget

    controller.refresh_main_tabs()

    assert widget.parent() is not None
    assert widget.isWindow() is False
    assert controller.app is not None
    assert controller.app.main_tab_widget.indexOf(widget) >= 0


def test_window_download_record_callback_leaves_plugin_refresh_to_controller():
    """Checks that plugin download records are not refreshed twice by window callbacks."""
    window = SimpleNamespace(
        feedback_service=Mock(),
        plugins_ui=Mock(),
    )
    record = DownloadRecord(
        id="rec4",
        display_name="Sample Plugin",
        target_kind=TargetKind.PLUGIN,
        download_status=DownloadStatus.DOWNLOADED,
        metadata={"plugin_id": "sample_plugin"},
    )

    on_downloads_record_updated(window, record)

    assert window.plugins_ui.mock_calls == []


def test_delete_plugin_reports_filesystem_error_with_plugin_path(temp_dir):
    """Checks that plugin delete errors use the plugin folder path in UI messages."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    plugin = SimpleNamespace(
        manifest=SimpleNamespace(name="Sample Plugin", version="1.0.0"),
        path="C:/plugins/sample_plugin",
    )
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.get_plugin.return_value = plugin
    assert controller.plugin_install_service is not None
    controller.plugin_install_service.delete_plugin.side_effect = PermissionError(
        13, "Permission denied", "C:/plugins/sample_plugin"
    )
    controller.feedback_service.show_message = Mock()
    controller.refresh_main_tabs = Mock()
    controller.render = Mock()

    controller.delete_plugin("sample_plugin")

    controller.feedback_service.show_message.assert_called_once_with(
        "error",
        "errors.error",
        tr("errors.permission_denied", path="C:/plugins/sample_plugin"),
    )


def test_delete_plugin_error_does_not_crash_if_feedback_fails(temp_dir):
    """Checks that plugin delete error handling survives feedback UI failures."""
    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    plugin = SimpleNamespace(
        manifest=SimpleNamespace(name="Sample Plugin", version="1.0.0"),
        path="C:/plugins/sample_plugin",
    )
    assert controller.plugin_runtime_service is not None
    controller.plugin_runtime_service.get_plugin.return_value = plugin
    assert controller.plugin_install_service is not None
    controller.plugin_install_service.delete_plugin.side_effect = PermissionError(
        13, "Permission denied", "C:/plugins/sample_plugin"
    )
    controller.feedback_service.show_message.side_effect = RuntimeError(
        "feedback failed"
    )
    controller.refresh_main_tabs = Mock()
    controller.render = Mock()

    controller.delete_plugin("sample_plugin")

    controller.refresh_main_tabs.assert_called_once()
    controller.render.assert_called_once()


@pytest.mark.parametrize("kind", ["theme", "plugin", "installed_plugin"])
def test_catalog_icons_fit_inside_border(qapp, qtbot, temp_dir, kind):
    from models.catalog_models import CatalogThemeEntry

    controller, _downloads_manager, _catalog = _make_controller(temp_dir)
    assert controller.app is not None
    controller.app.catalog_widget = QWidget()
    assert controller.app is not None
    qtbot.addWidget(controller.app.catalog_widget)
    image = QImage(32, 16, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.green)
    path = f"{temp_dir}/wide-icon.png"
    assert image.save(path)
    if kind == "theme":
        entry = CatalogThemeEntry("sample", "Sample", "Desc", "Author", "1.0", icon=path)
        card = controller._build_theme_card(entry, None)
    elif kind == "plugin":
        entry = CatalogPluginEntry(id="sample", name="Sample", description="Desc", author="Author", version="1.0", api_version=">=1.0", icon=path)
        card = controller._build_catalog_card(entry)
    else:
        card, label, _body, _actions = controller._build_card_shell()
        controller._set_local_icon(label, path)
    qtbot.addWidget(card)
    pixmap = vars(card)["icon_label"].pixmap()
    assert pixmap.width() == vars(card)["icon_label"].width() - 4
    assert pixmap.height() == pixmap.width() // 2
