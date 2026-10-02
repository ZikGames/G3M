"""Unit tests for test services."""

import json
import logging
import os
import time
import zipfile
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from config.config import PLUGIN_API_VERSION
from models.plugin_models import (
    CatalogPluginEntry,
    PluginCommunityFeed,
    PluginLaunchAction,
    PluginLaunchOption,
)
from services.localization_service import localization_service, tr
from services.plugins.catalog_service import PluginCatalogService
from services.plugins.install_service import PluginInstallService
from services.plugins.runtime_service import PluginRuntimeService
from services.plugins.state_service import PluginStateService
from services.plugins.support import (
    PluginValidationError,
    is_version_compatible,
    load_manifest,
    safe_extract_zip,
)


class _DummySettingsService:
    def __init__(self) -> None:
        self._files = {}

    def read_json(self, path):
        return self._files.get(path)

    def write_json(self, path, data):
        self._files[path] = json.loads(json.dumps(data))


@pytest.mark.parametrize("plugin_id", ["custom_saves_folders", "deltarune_save_manager"])
def test_published_plugin_matches_catalog_and_sources(plugin_id):
    catalog_root = Path(__file__).resolve().parents[3] / "catalog" / "plugins"
    source_root = catalog_root / plugin_id
    manifest = load_manifest(str(source_root / "plugin_config.json"))
    catalog = json.loads((catalog_root / "plugins.json").read_text(encoding="utf-8"))
    entry = next(plugin for plugin in catalog["plugins"] if plugin["id"] == plugin_id)
    assert entry["version"] == manifest.version
    assert entry["api_version"] == manifest.api_version
    assert is_version_compatible(PLUGIN_API_VERSION, manifest.api_version)
    def _normalize(name: str, content: bytes) -> bytes:
        if Path(name).suffix.lower() not in {".png", ".zip", ".ico", ".jpg", ".jpeg"}:
            return content.replace(b"\r\n", b"\n")
        return content

    sources = {
        path.relative_to(source_root).as_posix(): _normalize(path.name, path.read_bytes())
        for path in source_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    with zipfile.ZipFile(catalog_root / f"{plugin_id}.zip") as archive:
        assert {
            name: _normalize(name, archive.read(name))
            for name in archive.namelist()
            if not name.endswith("/")
        } == sources


@pytest.mark.parametrize("background_task", [False, True])
def test_plugin_interruption_only_propagates_for_cancellable_tasks(background_task):
    runtime = PluginRuntimeService.__new__(PluginRuntimeService)
    record = Mock(enabled=True, path="plugin", status="installed")
    plugin = Mock()
    plugin.on_before_restore_after_exit.side_effect = InterruptedError("interrupted")
    runtime._installed = {"sample": record}
    runtime._instances = {"sample": plugin}
    runtime._build_context = Mock()

    if background_task:
        with pytest.raises(InterruptedError):
            runtime.execute_hook_with_runtime("before_restore_after_exit", Mock())
        assert record.status == "installed"
    else:
        assert runtime.execute_hook("before_restore_after_exit") == []
        assert record.status == "broken"


def test_plugin_runtime_returns_only_validated_community_feeds():
    runtime = PluginRuntimeService.__new__(PluginRuntimeService)
    record = Mock(enabled=True)
    plugin = Mock()
    plugin.get_community_feeds.return_value = [
        {"id": "updates", "label": "Updates", "url": "https://example.com/rss"},
        PluginCommunityFeed("bad url", "Unsafe", "http://example.com/rss"),
        {"id": "missing", "label": "", "url": "https://example.com/rss"},
        PluginCommunityFeed(1, "Unsafe", "https://example.com/rss"),
        PluginCommunityFeed("unsafe", "Unsafe", None),
    ]
    runtime._installed = {"news_plugin": record}
    runtime._instances = {"news_plugin": plugin}
    runtime._build_ui_context = Mock()

    assert runtime.get_community_feeds() == [
        PluginCommunityFeed(
            id="plugin:news_plugin:updates",
            label="Updates",
            url="https://example.com/rss",
            plugin_id="news_plugin",
        )
    ]


def test_plugin_runtime_skips_launch_actions_with_non_string_text_fields():
    runtime = PluginRuntimeService.__new__(PluginRuntimeService)
    record = Mock(enabled=True)
    plugin = Mock()
    plugin.get_launch_actions.return_value = [
        PluginLaunchAction("refresh", "Refresh", "Refresh plugin data."),
        PluginLaunchAction(1, "Unsafe", ""),
        PluginLaunchAction("unsafe_label", None, ""),
        PluginLaunchAction("unsafe_description", "Unsafe", None),
    ]
    runtime._installed = {"news_plugin": record}
    runtime._instances = {"news_plugin": plugin}
    runtime._build_ui_context = Mock()

    assert runtime.get_launch_actions() == [
        PluginLaunchAction(
            id="plugin:news_plugin:refresh",
            label="Refresh",
            description="Refresh plugin data.",
            plugin_id="news_plugin",
        )
    ]


def test_plugin_runtime_returns_and_updates_validated_launch_options():
    runtime = PluginRuntimeService.__new__(PluginRuntimeService)
    record = Mock(enabled=True)
    plugin = Mock()
    plugin.get_launch_options.return_value = [
        PluginLaunchOption("save_slot", "Use save slot", checked=True),
        PluginLaunchOption("invalid", "Invalid", checked="yes"),
    ]
    runtime._installed = {"save_plugin": record}
    runtime._instances = {"save_plugin": plugin}
    runtime._build_ui_context = Mock(return_value="context")

    options = runtime.get_launch_options()

    assert options == [
        PluginLaunchOption(
            id="plugin:save_plugin:save_slot",
            label="Use save slot",
            checked=True,
            plugin_id="save_plugin",
        )
    ]
    plugin.on_launch_option_changed.return_value = False
    assert not runtime.set_launch_option(options[0], False)
    plugin.on_launch_option_changed.return_value = True
    assert runtime.set_launch_option(options[0], False)
    plugin.on_launch_option_changed.assert_called_with("context", "save_slot", False)


class _CatalogSpy:
    def __init__(self, entries=None) -> None:
        self.calls = []
        self.entries = entries or {}

    def is_loaded(self):
        return False

    def get_entry(self, plugin_id, *, load_if_needed=True):
        self.calls.append((plugin_id, load_if_needed))
        return self.entries.get(plugin_id)


def _write_plugin(plugins_dir, plugin_id="sample_plugin", api_version=">=1.0.0", version="1.0.0", *, hooks=None, relations=None):
    plugin_dir = os.path.join(plugins_dir, plugin_id)
    os.makedirs(os.path.join(plugin_dir, "lang"), exist_ok=True)
    with open(
        os.path.join(plugin_dir, "plugin_config.json"),
        "w",
        encoding="utf-8",
        ) as handle:
        json.dump(
            {
                "config_version": 1,
                "id": plugin_id,
                "name": f"plugins.{plugin_id}.name",
                "description": f"plugins.{plugin_id}.description",
                "author": "Tester",
                "version": version,
                "api_version": api_version,
                "entry": "plugin.py",
                "tags": ["tool"],
                "relations": relations or {},
                "hooks": hooks or [],
                "settings_schema": {},
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    with open(os.path.join(plugin_dir, "plugin.py"), "w", encoding="utf-8") as handle:
        handle.write(
            "class _Plugin:\n"
            "  def on_load(self, context):\n"
            "    self.context = context\n"
            "  def on_after_mod_apply_before_launch(self, context, *args):\n"
            "    self.hook_context = context\n"
            "    return True\n"
            "\n"
            "def create_plugin():\n"
            "  return _Plugin()\n"
        )
    with open(
        os.path.join(plugin_dir, "lang", "lang_en.json"),
        "w",
        encoding="utf-8",
        ) as handle:
        json.dump(
            {
                "name": "Sample Plugin",
                "description": "Sample description",
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )


def _write_dataclass_plugin(plugins_dir, plugin_id="dataclass_plugin"):
    plugin_dir = os.path.join(plugins_dir, plugin_id)
    os.makedirs(os.path.join(plugin_dir, "lang"), exist_ok=True)
    with open(
        os.path.join(plugin_dir, "plugin_config.json"),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            {
                "config_version": 1,
                "id": plugin_id,
                "name": f"plugins.{plugin_id}.name",
                "description": f"plugins.{plugin_id}.description",
                "author": "Tester",
                "version": "1.0.0",
                "api_version": ">=1.0.0",
                "entry": "plugin.py",
                "tags": ["tool"],
                "relations": {},
                "hooks": [],
                "settings_schema": {},
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    with open(os.path.join(plugin_dir, "plugin.py"), "w", encoding="utf-8") as handle:
        handle.write(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class _State:\n"
            "  value: str = 'ok'\n"
            "\n"
            "class _Plugin:\n"
            "  def __init__(self):\n"
            "    self.state = _State()\n"
            "\n"
            "def create_plugin():\n"
            "  return _Plugin()\n"
        )
    with open(
        os.path.join(plugin_dir, "lang", "lang_en.json"),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            {
                "name": "Dataclass Plugin",
                "description": "Dataclass plugin description",
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )


def test_plugin_state_service_persists_settings_and_filters(temp_dir):
    """Checks that plugin state service persists settings and filters."""
    settings_service = _DummySettingsService()
    service = PluginStateService(settings_service, temp_dir)
    service.set_enabled("alpha", True)
    service.set_plugin_setting("alpha", "path", "C:/test")
    service.set_filters(installed_only=True, tags=["tool", "bad_tag"])
    service.set_filters(installed_only=False, tags=["animated", "tool"], themes=True)
    reloaded = PluginStateService(settings_service, temp_dir)
    assert reloaded.is_enabled("alpha") is True
    assert reloaded.get_plugin_setting("alpha", "path") == "C:/test"
    assert reloaded.get_filters() == {"installed_only": True, "tags": ["tool"]}
    assert reloaded.get_filters(themes=True) == {"installed_only": False, "tags": ["animated"]}


def test_plugin_catalog_service_returns_empty_when_cache_is_empty(temp_dir):
    """Checks that plugin catalog service returns empty when cache is empty."""
    app_state = Mock()
    app_state.network_session = None
    settings_service = _DummySettingsService()
    service = PluginCatalogService(app_state, settings_service, temp_dir)
    catalog = service.load_catalog()
    assert service.is_loaded() is False
    assert catalog == {}
    assert service.get_entry("fallback_plugin") is None


def test_plugin_catalog_service_uses_in_memory_cache(temp_dir):
    """Checks that plugin catalog service uses in memory cache."""
    app_state = Mock()
    app_state.network_session = None
    settings_service = _DummySettingsService()
    service = PluginCatalogService(app_state, settings_service, temp_dir)
    service._catalog = {"plugins": [{"id": "cached_plugin", "name": "Cached"}]}
    service._catalog_loaded_at = time.time()

    catalog = service.load_catalog()

    assert catalog["plugins"][0]["id"] == "cached_plugin"
    assert service.get_entry("cached_plugin").name == "Cached"


def test_catalog_skips_malformed_records_and_preserves_themes_on_partial_refresh(temp_dir):
    app_state = Mock()
    response = Mock()
    response.json.return_value = {"plugins": [None, 123, {"id": "sample", "tags": "tool", "relations": None}]}
    app_state.network_session.get.side_effect = [response, OSError("Offline")]
    service = PluginCatalogService(app_state, _DummySettingsService(), temp_dir)
    service._catalog = {"themes": [None, {"id": "retained", "tags": None}]}

    service.refresh_catalog()

    plugins = service.list_plugins()
    themes = service.list_themes()
    assert [entry.id for entry in plugins] == ["sample"]
    assert plugins[0].tags == []
    assert plugins[0].relations == {}
    assert [entry.id for entry in themes] == ["retained"]
    assert themes[0].tags == []


@pytest.mark.parametrize("hook", ["app_ready", "app_shutdown", "navigation_actions", "game_registry", "background_task"])
def test_removed_plugin_capabilities_are_incompatible_before_loading(tmp_path, hook):
    _write_plugin(tmp_path, "legacy_plugin", api_version=">=1.1.0", hooks=[hook])
    state = PluginStateService(_DummySettingsService(), str(tmp_path))
    state.set_enabled("legacy_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}), feedback_service=Mock(), settings_service=Mock(),
        profile_service=Mock(), game_registry_service=Mock(), plugin_state_service=state,
        plugin_catalog_service=_CatalogSpy(), plugins_dir=str(tmp_path),
    )

    record = runtime.scan_installed_plugins()["legacy_plugin"]

    assert record.status == "installed"
    assert record.compatible is False
    assert record.error == tr("plugins.error_incompatible_api", plugin="legacy_plugin")
    assert record.manifest.hooks == [hook]
    assert "legacy_plugin" not in runtime._instances
    assert runtime.get_settings_widget("legacy_plugin") is None
    assert runtime.enable_plugin("legacy_plugin")[0] is False


def test_legacy_settings_and_game_started_declarations_keep_supported_callbacks(tmp_path):
    _write_plugin(
        tmp_path, "legacy_plugin", api_version=">=1.1.0",
        hooks=["settings_view", "before_mod_apply", "after_game_started"],
    )
    (tmp_path / "legacy_plugin" / "plugin.py").write_text(
        "class Plugin:\n"
        "    def on_before_mod_apply(self, context, *args): return 'applied'\n"
        "    def on_after_game_started(self, context, *args): return 'started'\n"
        "    def create_settings_widget(self, context, parent): return parent\n"
        "def create_plugin(): return Plugin()\n",
        encoding="utf-8",
    )
    state = PluginStateService(_DummySettingsService(), str(tmp_path))
    state.set_enabled("legacy_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}), feedback_service=Mock(), settings_service=Mock(),
        profile_service=Mock(), game_registry_service=Mock(), plugin_state_service=state,
        plugin_catalog_service=_CatalogSpy(), plugins_dir=str(tmp_path),
    )

    record = runtime.scan_installed_plugins()["legacy_plugin"]

    assert record.compatible
    assert record.status != "broken"
    assert runtime.execute_hook("before_mod_apply") == ["applied"]
    assert runtime.execute_hook("after_game_started", False) == ["started"]
    parent = object()
    assert runtime.get_settings_widget("legacy_plugin", parent) is parent


def test_incompatible_required_plugin_cannot_satisfy_dependency(tmp_path):
    _write_plugin(tmp_path, "legacy_plugin", hooks=["game_registry"])
    _write_plugin(tmp_path, "dependent_plugin", relations={"legacy_plugin": "require"})
    _write_plugin(tmp_path, "transitive_plugin", relations={"dependent_plugin": "require"})
    state = PluginStateService(_DummySettingsService(), str(tmp_path))
    state.set_enabled("legacy_plugin", True)
    state.set_enabled("dependent_plugin", True)
    state.set_enabled("transitive_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}), feedback_service=Mock(), settings_service=Mock(),
        profile_service=Mock(), game_registry_service=Mock(), plugin_state_service=state,
        plugin_catalog_service=_CatalogSpy(), plugins_dir=str(tmp_path),
    )
    runtime.scan_installed_plugins()

    success, error = runtime.enable_plugin("dependent_plugin")

    assert success is False
    assert error == tr("plugins.error_missing_dependencies", plugin="dependent_plugin")
    assert "dependent_plugin" not in runtime._instances
    assert "transitive_plugin" not in runtime._instances
    for plugin_id in ("dependent_plugin", "transitive_plugin"):
        record = runtime.get_plugin(plugin_id)
        assert record.status == "broken"
        assert not record.enabled
        assert record.error == tr("plugins.error_missing_dependencies", plugin=plugin_id)
        runtime.disable_plugin(plugin_id, persist=False)
        assert record.status == "broken"
    assert runtime.enable_plugin("transitive_plugin")[0] is False


@pytest.mark.parametrize("dependency_fails", [False, True])
def test_plugin_reload_waits_for_successfully_enabled_dependencies(tmp_path, monkeypatch, dependency_fails):
    _write_plugin(tmp_path, "a_dependent", relations={"z_required": "require"})
    _write_plugin(tmp_path, "z_required")
    state = PluginStateService(_DummySettingsService(), str(tmp_path))
    for plugin_id in ("a_dependent", "z_required"):
        state.set_enabled(plugin_id, True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}), feedback_service=Mock(), settings_service=Mock(),
        profile_service=Mock(), game_registry_service=Mock(), plugin_state_service=state,
        plugin_catalog_service=_CatalogSpy(), plugins_dir=str(tmp_path),
    )
    calls = []

    def load_factory(plugin_id, _path):
        def factory():
            calls.append(plugin_id)
            if plugin_id == "z_required" and dependency_fails:
                raise RuntimeError("required factory failed")
            if plugin_id == "a_dependent":
                assert "z_required" in runtime._enabled_instances
            return object()
        return factory

    monkeypatch.setattr("services.plugins.runtime_service.load_plugin_factory", load_factory)

    installed = runtime.scan_installed_plugins()

    if dependency_fails:
        assert calls == ["z_required"]
        assert not runtime._instances
        assert not runtime._enabled_instances
        assert installed["a_dependent"].error == tr("plugins.error_missing_dependencies", plugin="a_dependent")
    else:
        assert calls == ["z_required", "a_dependent"]
        assert runtime._enabled_instances == {"z_required", "a_dependent"}


def test_plugin_reload_does_not_enable_cached_instance_after_enable_failure(tmp_path):
    _write_plugin(tmp_path)
    state = PluginStateService(_DummySettingsService(), str(tmp_path))
    state.set_enabled("sample_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}), feedback_service=Mock(), settings_service=Mock(),
        profile_service=Mock(), game_registry_service=Mock(), plugin_state_service=state,
        plugin_catalog_service=_CatalogSpy(), plugins_dir=str(tmp_path),
    )
    plugin = Mock()
    plugin.on_enable.side_effect = OSError("enable failed")
    runtime._instances["sample_plugin"] = plugin

    installed = runtime.scan_installed_plugins()

    assert installed["sample_plugin"].status == "broken"
    assert "sample_plugin" not in runtime._enabled_instances
    assert "sample_plugin" not in runtime._instances
    assert not installed["sample_plugin"].enabled
    assert runtime.execute_hook("before_mod_apply") == []


def test_plugin_runtime_scan_merges_localizations_without_catalog_load(temp_dir):
    """Checks that plugin runtime scan merges localizations without catalog load."""
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    catalog_spy = _CatalogSpy()
    _write_plugin(temp_dir)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=catalog_spy,
        plugins_dir=temp_dir,
    )
    installed = runtime.scan_installed_plugins()
    assert "sample_plugin" in installed
    assert catalog_spy.calls == [("sample_plugin", False)]
    assert localization_service.get_text("plugins.sample_plugin.name") == "Sample Plugin"
    assert localization_service.get_text("plugins.sample_plugin.description") == "Sample description"
    localization_service.clear_plugin_strings("sample_plugin")


def test_plugin_runtime_update_available_requires_newer_catalog_version(temp_dir):
    """Checks that plugin updates are offered only for strict catalog upgrades."""
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, version="1.1.2")
    catalog_entry = CatalogPluginEntry(
        id="sample_plugin",
        name="Sample Plugin",
        description="Sample description",
        author="Tester",
        version="1.1.1",
        api_version=">=1.0.0",
        download_link="https://example.invalid/sample.zip",
    )
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy({"sample_plugin": catalog_entry}),
        plugins_dir=temp_dir,
    )

    installed = runtime.scan_installed_plugins(resolve_catalog=True)
    assert installed["sample_plugin"].update_available is False

    catalog_entry.version = "1.1.3"
    installed = runtime.scan_installed_plugins(resolve_catalog=True)
    assert installed["sample_plugin"].update_available is True
    localization_service.clear_plugin_strings("sample_plugin")


def test_plugin_runtime_loads_dataclass_plugin(temp_dir):
    """Checks that plugin runtime loads plugins that use dataclasses."""
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_dataclass_plugin(temp_dir)
    state_service.set_enabled("dataclass_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    installed = runtime.scan_installed_plugins()

    assert "dataclass_plugin" in installed
    assert installed["dataclass_plugin"].status != "broken"


def test_plugin_runtime_scan_formats_validation_errors(temp_dir, caplog):
    """Checks that broken plugin scan errors are localized for the UI."""
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    plugin_dir = os.path.join(temp_dir, "broken_plugin")
    os.makedirs(plugin_dir, exist_ok=True)
    with open(
        os.path.join(plugin_dir, "plugin_config.json"),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            {
                "config_version": 1,
                "id": "broken_plugin",
                "name": "Broken Plugin",
                "description": "Desc",
                "author": "Tester",
                "version": "1.0.0",
                "api_version": ">=1.0.0",
                "entry": "missing.py",
            },
            handle,
        )
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    with caplog.at_level(logging.INFO, logger="services.plugins.runtime_service"):
        installed = runtime.scan_installed_plugins()

    assert installed["broken_plugin"].error == tr(
        "plugins.error_missing_entry", path=plugin_dir
    )
    record = next(
        record
        for record in caplog.records
        if record.name == "services.plugins.runtime_service"
    )
    assert record.levelno == logging.INFO
    assert record.exc_info is None
    assert record.getMessage() == "PluginRuntimeService: skipped invalid plugin broken_plugin: missing_entry"


def test_plugin_runtime_keeps_display_metadata_for_invalid_plugin(temp_dir):
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    _write_plugin(temp_dir, "legacy_plugin", api_version=">=1.1.0")
    manifest_path = os.path.join(temp_dir, "legacy_plugin", "plugin_config.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["hooks"] = ["removed_hook"]
    manifest["icon"] = "icon.png"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    state_service.set_enabled("legacy_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    plugin = runtime.scan_installed_plugins()["legacy_plugin"]

    assert plugin.status == "broken"
    assert plugin.compatible is True
    assert plugin.manifest is not None
    assert plugin.manifest.entry == ""
    assert plugin.manifest.icon == "icon.png"
    assert plugin.manifest.name == "plugins.legacy_plugin.name"
    assert localization_service.get_text(plugin.manifest.name) == "Sample Plugin"
    assert "legacy_plugin" not in runtime._instances
    assert runtime.get_settings_widget("legacy_plugin") is None


def test_plugin_runtime_marks_failed_settings_widget_as_broken(caplog):
    localization_service.load_language("en")
    runtime = PluginRuntimeService.__new__(PluginRuntimeService)
    record = Mock(status="installed", path="C:/plugins/old_plugin")
    runtime._installed = {"old_plugin": record}
    runtime._instances = {}
    runtime._load_instance = Mock(
        side_effect=ModuleNotFoundError(
            "No module named 'utils.mod.config_parser'",
            name="utils.mod.config_parser",
        )
    )

    with caplog.at_level(logging.WARNING, logger="services.plugins.runtime_service"):
        assert runtime.get_settings_widget("old_plugin") is None

    assert record.status == "broken"
    assert record.error == tr(
        "plugins.error_missing_module",
        plugin="old_plugin",
        module="utils.mod.config_parser",
    )
    log_record = next(
        entry
        for entry in caplog.records
        if entry.name == "services.plugins.runtime_service"
    )
    assert log_record.levelno == logging.WARNING
    assert log_record.exc_info is None
    assert "settings widget unavailable for old_plugin" in log_record.getMessage()


def test_plugin_install_accepts_newer_plugin_api_requirement(temp_dir):
    """Checks that Plugin API mismatch does not block installation."""
    source_root = os.path.join(temp_dir, "source")
    plugins_dir = os.path.join(temp_dir, "installed")
    _write_plugin(source_root, "future_plugin", api_version=">=99.0.0")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    runtime = Mock()
    service = PluginInstallService(state_service, runtime, plugins_dir)

    installed_id = service.install_path(
        os.path.join(source_root, "future_plugin"),
        source="manual",
    )

    assert installed_id == "future_plugin"
    assert os.path.isdir(os.path.join(plugins_dir, "future_plugin"))
    runtime.scan_installed_plugins.assert_not_called()


def test_plugin_runtime_does_not_load_newer_plugin_api_requirement(temp_dir):
    """Checks that an API mismatch remains visible without loading the plugin."""
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, "future_plugin", api_version=">=99.0.0")
    state_service.set_enabled("future_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    installed = runtime.scan_installed_plugins()

    assert installed["future_plugin"].compatible is False
    assert installed["future_plugin"].enabled is True
    assert installed["future_plugin"].status == "installed"
    assert "future_plugin" not in runtime._instances
    assert "future_plugin" not in runtime._enabled_instances


def test_plugin_runtime_cannot_enable_incompatible_plugin(temp_dir):
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, "future_plugin", api_version=">=99.0.0")
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )
    runtime.scan_installed_plugins()

    enabled, error = runtime.enable_plugin("future_plugin")

    assert not enabled
    assert error == tr("plugins.error_incompatible_api", plugin="future_plugin")
    assert not state_service.is_enabled("future_plugin")
    assert "future_plugin" not in runtime._instances


def test_plugin_runtime_propagates_critical_hook_errors():
    runtime = PluginRuntimeService.__new__(PluginRuntimeService)
    record = Mock(enabled=True, path="plugin", status="installed")
    plugin = Mock()
    plugin.on_before_restore_after_exit.side_effect = RuntimeError("restore failed")
    runtime._installed = {"sample": record}
    runtime._instances = {"sample": plugin}
    runtime._build_context = Mock()

    with pytest.raises(RuntimeError, match="restore failed"):
        runtime.execute_hook_with_runtime(
            "before_restore_after_exit", None, raise_errors=True
        )

    assert record.status == "broken"


def test_plugin_runtime_reports_enabled_hook_and_passes_task_runtime(temp_dir):
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, "hook_plugin")
    state_service.set_enabled("hook_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    runtime.scan_installed_plugins()

    assert runtime.has_enabled_hook("after_mod_apply_before_launch") is True
    task_runtime = Mock()
    results = runtime.execute_hook_with_runtime(
        "after_mod_apply_before_launch",
        task_runtime,
        {"deltarune_1": []},
        False,
    )

    assert results == [True]
    assert runtime._instances["hook_plugin"].hook_context.task_runtime is task_runtime


def test_plugin_runtime_reports_enabled_shortcut_hook_and_passes_shortcut_context(
    temp_dir,
):
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, "shortcut_hook_plugin")
    plugin_dir = os.path.join(temp_dir, "shortcut_hook_plugin")
    with open(os.path.join(plugin_dir, "plugin_config.json"), encoding="utf-8") as handle:
        plugin_config = json.load(handle)
    plugin_config["hooks"] = [
        "before_mod_apply_shortcut",
        "after_mod_apply_before_launch_shortcut",
    ]
    with open(
        os.path.join(plugin_dir, "plugin_config.json"),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(plugin_config, handle, ensure_ascii=False, indent=2)
    with open(os.path.join(plugin_dir, "plugin.py"), "w", encoding="utf-8") as handle:
        handle.write(
            "class _Plugin:\n"
            "  def on_before_mod_apply_shortcut(self, context, shortcut_context, *args):\n"
            "    shortcut_context.set_plugin_state('shortcut_hook_plugin', {'selected': 'alpha'})\n"
            "    shortcut_context.add_summary_line('Collection', 'alpha')\n"
            "    self.capture_context = context\n"
            "    return True\n"
            "  def on_after_mod_apply_before_launch_shortcut(self, context, shortcut_context, *args):\n"
            "    self.launch_context = context\n"
            "    self.shortcut_context = shortcut_context\n"
            "    return shortcut_context.get_plugin_state('shortcut_hook_plugin')\n"
            "\n"
            "def create_plugin():\n"
            "  return _Plugin()\n"
        )
    state_service.set_enabled("shortcut_hook_plugin", True)
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    runtime.scan_installed_plugins()

    from services.plugins.shortcut_service import ShortcutPluginContext

    shortcut_context = ShortcutPluginContext({"game_id": "deltarune"})
    capture_results = runtime.execute_hook(
        "before_mod_apply_shortcut",
        shortcut_context,
    )
    launch_results = runtime.execute_hook(
        "after_mod_apply_before_launch_shortcut",
        shortcut_context,
    )

    assert runtime.has_enabled_hook("before_mod_apply_shortcut") is True
    assert capture_results == [True]
    assert launch_results == [{"selected": "alpha"}]
    assert shortcut_context.plugin_states == {
        "shortcut_hook_plugin": {"selected": "alpha"}
    }
    assert shortcut_context.summary_lines == [("Collection", "alpha")]


def test_headless_plugin_runtime_ignores_corrupted_plugin_state(temp_dir, monkeypatch):
    from services.plugins.shortcut_service import build_headless_plugin_runtime

    user_root = os.path.join(temp_dir, "user")
    plugins_dir = os.path.join(user_root, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    _write_plugin(plugins_dir, "headless_plugin")
    with open(os.path.join(plugins_dir, "plugins_data.json"), "w", encoding="utf-8") as handle:
        handle.write("{broken json")
    monkeypatch.setattr("services.plugins.shortcut_service.get_user_data_root", lambda: user_root)

    runtime = build_headless_plugin_runtime({"language": "en"})

    assert runtime is not None
    assert "headless_plugin" in runtime._installed


def test_plugin_state_service_recovers_when_settings_read_raises(temp_dir):
    settings_service = Mock()
    settings_service.read_json.side_effect = OSError("state unreadable")
    settings_service.write_json = Mock()

    state_service = PluginStateService(settings_service, temp_dir)

    assert state_service.is_enabled("missing_plugin") is False
    settings_service.write_json.assert_called_once()


def test_plugin_runtime_context_uses_plugin_scoped_feedback(temp_dir, monkeypatch, qapp):
    from ui.common import feedback as feedback_module
    from ui.common.feedback import FeedbackManager

    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, "sample_plugin")
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=FeedbackManager(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )
    runtime.scan_installed_plugins()

    box = Mock()
    box.Icon = Mock()
    box.StandardButton = Mock(Yes=1, No=2)
    box.setIcon = Mock()
    box.setWindowTitle = Mock()
    box.setText = Mock()
    box.setStandardButtons = Mock()
    box.setDefaultButton = Mock()
    box.exec = Mock(return_value=1)
    factory = Mock(return_value=box)
    factory.Icon = feedback_module.QMessageBox.Icon
    factory.StandardButton = feedback_module.QMessageBox.StandardButton
    monkeypatch.setattr(feedback_module, "QMessageBox", factory)

    context = runtime._build_context("sample_plugin")
    context.feedback_service.ask_question("name", "description")

    box.setWindowTitle.assert_called_once_with("Sample Plugin")
    assert "Sample description" in box.setText.call_args.args[0]


def test_plugin_runtime_context_hides_unrelated_services(temp_dir):
    localization_service.clear_plugin_strings()
    localization_service.load_language("en")
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, temp_dir)
    _write_plugin(temp_dir, "sample_plugin")
    runtime = PluginRuntimeService(
        app_state=Mock(local_config={}),
        feedback_service=Mock(),
        settings_service=Mock(),
        profile_service=Mock(),
        game_registry_service=Mock(),
        plugin_state_service=state_service,
        plugin_catalog_service=_CatalogSpy(),
        plugins_dir=temp_dir,
    )

    runtime.scan_installed_plugins()
    context = runtime._build_context("sample_plugin")

    assert not hasattr(context, "used_mods_service")
    assert not hasattr(context, "downloads_manager")


def test_plugin_install_service_accepts_plugin_folder(temp_dir):
    """Checks that plugin install service accepts plugin folder."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "folder_plugin")
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=Mock(scan_installed_plugins=Mock(), enable_plugin=Mock(return_value=(True, ""))),
        plugins_dir=plugins_dir,
    )

    plugin_id = install_service.install_path(source_dir, source="manual")

    assert plugin_id == "folder_plugin"
    assert os.path.isfile(os.path.join(plugins_dir, "folder_plugin", "plugin_config.json"))
    assert os.path.isfile(os.path.join(source_dir, "folder_plugin", "plugin_config.json"))
    assert os.path.isfile(os.path.join(source_dir, "folder_plugin", "plugin.py"))
    assert state_service.get_install_meta("folder_plugin")["local"] is True
    install_service.plugin_runtime_service.scan_installed_plugins.assert_not_called()


def test_plugin_install_service_preserves_state_when_reinstalling_same_plugin(temp_dir):
    """Checks that plugin update/reinstall preserves saved state for the same plugin id."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "stateful_plugin")
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=Mock(scan_installed_plugins=Mock(), enable_plugin=Mock(return_value=(True, ""))),
        plugins_dir=plugins_dir,
    )
    install_service.install_path(source_dir, source="catalog", catalog_plugin_version="1.0.0")
    state_service.set_enabled("stateful_plugin", True)
    state_service.set_plugin_setting("stateful_plugin", "folder", "SOJ")

    plugin_id = install_service.install_path(
        source_dir,
        source="catalog",
        catalog_plugin_version="1.0.1",
    )

    assert plugin_id == "stateful_plugin"
    assert state_service.is_enabled("stateful_plugin") is True
    assert state_service.get_plugin_setting("stateful_plugin", "folder") == "SOJ"
    assert state_service.get_install_meta("stateful_plugin")["catalog_plugin_version"] == "1.0.1"


def test_plugin_install_service_preserves_plugin_runtime_files_when_updating(temp_dir):
    """Checks that plugin update keeps files created by the installed plugin."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "runtime_data_plugin")
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=Mock(scan_installed_plugins=Mock()),
        plugins_dir=plugins_dir,
    )
    install_service.install_path(source_dir, source="catalog", catalog_plugin_version="1.0.0")
    installed_dir = os.path.join(plugins_dir, "runtime_data_plugin")
    runtime_file = os.path.join(installed_dir, "runtime_data", "save.json")
    os.makedirs(os.path.dirname(runtime_file), exist_ok=True)
    with open(runtime_file, "w", encoding="utf-8") as handle:
        handle.write('{"selected": "SOJ"}')
    with open(os.path.join(source_dir, "runtime_data_plugin", "plugin.py"), "w", encoding="utf-8") as handle:
        handle.write("def create_plugin():\n  return object()\n")

    plugin_id = install_service.install_path(
        source_dir,
        source="catalog",
        catalog_plugin_version="1.0.1",
    )

    assert plugin_id == "runtime_data_plugin"
    assert os.path.isfile(runtime_file)
    with open(runtime_file, encoding="utf-8") as handle:
        assert handle.read() == '{"selected": "SOJ"}'
    with open(os.path.join(installed_dir, "plugin.py"), encoding="utf-8") as handle:
        assert "return object()" in handle.read()


def test_plugin_install_service_keeps_existing_plugin_when_update_file_is_busy(
    temp_dir, monkeypatch
):
    """Checks that a failed file replacement does not remove installed plugin data."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "busy_plugin")
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=Mock(scan_installed_plugins=Mock()),
        plugins_dir=plugins_dir,
    )
    install_service.install_path(source_dir, source="catalog", catalog_plugin_version="1.0.0")
    installed_dir = os.path.join(plugins_dir, "busy_plugin")
    runtime_file = os.path.join(installed_dir, "runtime_data", "save.json")
    os.makedirs(os.path.dirname(runtime_file), exist_ok=True)
    with open(runtime_file, "w", encoding="utf-8") as handle:
        handle.write('{"selected": "SOJ"}')
    old_plugin_path = os.path.join(installed_dir, "plugin.py")
    with open(old_plugin_path, encoding="utf-8") as handle:
        old_plugin_body = handle.read()
    with open(os.path.join(source_dir, "busy_plugin", "plugin.py"), "w", encoding="utf-8") as handle:
        handle.write("def create_plugin():\n  return object()\n")
    real_replace = os.replace

    def fail_plugin_replace(src, dst):
        if os.path.normcase(dst) == os.path.normcase(old_plugin_path):
            raise PermissionError("file is busy")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_plugin_replace)

    with pytest.raises(PluginValidationError) as exc_info:
        install_service.install_path(
            source_dir,
            source="catalog",
            catalog_plugin_version="1.0.1",
        )

    assert "installation_failed" in str(exc_info.value)
    assert os.path.isfile(runtime_file)
    with open(runtime_file, encoding="utf-8") as handle:
        assert handle.read() == '{"selected": "SOJ"}'
    with open(old_plugin_path, encoding="utf-8") as handle:
        assert handle.read() == old_plugin_body
    assert state_service.get_install_meta("busy_plugin")["catalog_plugin_version"] == "1.0.0"


def test_plugin_install_service_disables_enabled_plugin_while_updating(temp_dir):
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "enabled_update_plugin")
    runtime = Mock(scan_installed_plugins=Mock(), disable_plugin=Mock(), enable_plugin=Mock(return_value=(True, "")))
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=runtime,
        plugins_dir=plugins_dir,
    )
    install_service.install_path(source_dir, source="catalog", catalog_plugin_version="1.0.0")
    state_service.set_enabled("enabled_update_plugin", True)
    with open(os.path.join(source_dir, "enabled_update_plugin", "plugin.py"), "w", encoding="utf-8") as handle:
        handle.write("def create_plugin():\n  return object()\n")

    plugin_id = install_service.install_path(
        source_dir,
        source="catalog",
        catalog_plugin_version="1.0.1",
    )

    assert plugin_id == "enabled_update_plugin"
    runtime.disable_plugin.assert_called_once_with("enabled_update_plugin", persist=False)
    runtime.scan_installed_plugins.assert_called_once_with(resolve_catalog=False)
    runtime.enable_plugin.assert_called_once_with("enabled_update_plugin")
    assert runtime.mock_calls.index(call.disable_plugin("enabled_update_plugin", persist=False)) < runtime.mock_calls.index(
        call.scan_installed_plugins(resolve_catalog=False)
    )
    assert runtime.mock_calls.index(call.scan_installed_plugins(resolve_catalog=False)) < runtime.mock_calls.index(
        call.enable_plugin("enabled_update_plugin")
    )
    assert state_service.is_enabled("enabled_update_plugin") is True


def test_plugin_install_service_does_not_reenable_plugin_after_failed_update(
    temp_dir, monkeypatch
):
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "failed_update_plugin")
    runtime = Mock(scan_installed_plugins=Mock(), disable_plugin=Mock(), enable_plugin=Mock(return_value=(True, "")))
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=runtime,
        plugins_dir=plugins_dir,
    )
    install_service.install_path(source_dir, source="catalog", catalog_plugin_version="1.0.0")
    state_service.set_enabled("failed_update_plugin", True)
    installed_dir = os.path.join(plugins_dir, "failed_update_plugin")
    old_plugin_path = os.path.join(installed_dir, "plugin.py")
    real_replace = os.replace

    def fail_plugin_replace(src, dst):
        if os.path.normcase(dst) == os.path.normcase(old_plugin_path):
            raise PermissionError("file is busy")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_plugin_replace)

    with pytest.raises(PluginValidationError):
        install_service.install_path(
            source_dir,
            source="catalog",
            catalog_plugin_version="1.0.1",
        )

    runtime.disable_plugin.assert_called_once_with("failed_update_plugin", persist=False)
    runtime.enable_plugin.assert_not_called()
    assert state_service.is_enabled("failed_update_plugin") is True
    assert state_service.get_install_meta("failed_update_plugin")["catalog_plugin_version"] == "1.0.0"


def test_plugin_install_service_does_not_toggle_disabled_plugin_when_updating(temp_dir):
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_plugin")
    _write_plugin(source_dir, "disabled_update_plugin")
    runtime = Mock(scan_installed_plugins=Mock(), disable_plugin=Mock(), enable_plugin=Mock(return_value=(True, "")))
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=runtime,
        plugins_dir=plugins_dir,
    )
    install_service.install_path(source_dir, source="catalog", catalog_plugin_version="1.0.0")

    plugin_id = install_service.install_path(
        source_dir,
        source="catalog",
        catalog_plugin_version="1.0.1",
    )

    assert plugin_id == "disabled_update_plugin"
    runtime.disable_plugin.assert_not_called()
    runtime.enable_plugin.assert_not_called()
    assert state_service.is_enabled("disabled_update_plugin") is False


def test_plugin_install_service_accepts_plugin_zip(temp_dir):
    """Checks that plugin install service accepts plugin zip."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_zip")
    _write_plugin(source_dir, "zip_plugin")
    archive_path = os.path.join(temp_dir, "plugin.zip")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(source_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                archive.write(file_path, os.path.relpath(file_path, source_dir))
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=Mock(scan_installed_plugins=Mock()),
        plugins_dir=plugins_dir,
    )

    plugin_id = install_service.install_path(archive_path, source="manual")

    assert plugin_id == "zip_plugin"
    assert os.path.isfile(os.path.join(plugins_dir, "zip_plugin", "plugin_config.json"))


def test_plugin_install_service_accepts_deeply_nested_plugin_zip(temp_dir):
    """Checks that plugin install service accepts deeply nested plugin zip."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    source_dir = os.path.join(temp_dir, "source_nested")
    _write_plugin(source_dir, "nested_plugin")
    archive_path = os.path.join(temp_dir, "nested_plugin.zip")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(source_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                archive.write(
                    file_path,
                    os.path.join(
                        "level1",
                        "level2",
                        "level3",
                        os.path.relpath(file_path, source_dir),
                    ),
                )
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=Mock(scan_installed_plugins=Mock()),
        plugins_dir=plugins_dir,
    )

    plugin_id = install_service.install_path(archive_path, source="manual")

    assert plugin_id == "nested_plugin"
    assert os.path.isfile(
        os.path.join(plugins_dir, "nested_plugin", "plugin_config.json")
    )


def test_plugin_install_delete_does_not_touch_runtime_from_install_service(temp_dir):
    """Checks that plugin file deletion does not execute runtime hooks directly."""
    settings_service = _DummySettingsService()
    state_service = PluginStateService(settings_service, os.path.join(temp_dir, "state"))
    plugins_dir = os.path.join(temp_dir, "plugins")
    _write_plugin(plugins_dir, "delete_plugin")
    runtime = Mock()
    install_service = PluginInstallService(
        plugin_state_service=state_service,
        plugin_runtime_service=runtime,
        plugins_dir=plugins_dir,
    )
    state_service.set_enabled("delete_plugin", True)
    state_service.set_plugin_setting("delete_plugin", "folder", "SOJ")
    state_service.set_install_meta("delete_plugin", source="catalog")

    install_service.delete_plugin("delete_plugin")

    runtime.disable_plugin.assert_not_called()
    assert not os.path.exists(os.path.join(plugins_dir, "delete_plugin"))
    assert state_service.is_enabled("delete_plugin") is False
    assert state_service.get_plugin_settings("delete_plugin") == {}
    assert state_service.get_install_meta("delete_plugin") == {}


def test_plugin_zip_extraction_rejects_excessive_uncompressed_size(
    temp_dir, monkeypatch
):
    """Checks that plugin archives are size-checked before extraction."""
    import services.plugins.support as plugin_support

    archive_path = os.path.join(temp_dir, "huge_plugin.zip")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin_config.json", "{}")
    monkeypatch.setattr(plugin_support, "MAX_PLUGIN_ARCHIVE_UNCOMPRESSED_BYTES", 1)

    with pytest.raises(PluginValidationError) as exc_info:
        safe_extract_zip(archive_path, os.path.join(temp_dir, "out"))
    assert str(exc_info.value) == "archive_too_large"


def test_plugin_zip_extraction_rejects_too_many_members(temp_dir, monkeypatch):
    """Checks that plugin archives are member-count checked before extraction."""
    import services.plugins.support as plugin_support

    archive_path = os.path.join(temp_dir, "many_plugin.zip")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.txt", "a")
        archive.writestr("b.txt", "b")
    monkeypatch.setattr(plugin_support, "MAX_PLUGIN_ARCHIVE_MEMBERS", 1)

    with pytest.raises(PluginValidationError) as exc_info:
        safe_extract_zip(archive_path, os.path.join(temp_dir, "out"))
    assert str(exc_info.value) == "archive_too_many_files"
