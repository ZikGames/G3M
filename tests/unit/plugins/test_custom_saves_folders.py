from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_PATH = (
    Path(__file__).resolve().parents[3]
    / "catalog"
    / "plugins"
    / "custom_saves_folders"
    / "plugin.py"
)


class _PluginSettings:
    def __init__(self) -> None:
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class _Feedback:
    def __init__(self) -> None:
        self.statuses = []

    def update_status(self, *args) -> None:
        self.statuses.append(args)


class _Localization:
    @staticmethod
    def get_plugin_tr(_plugin_id) -> Callable[..., str]:
        return lambda key, **_kwargs: key

    @staticmethod
    def get_text(key) -> str:
        return key


class _G3MTool:
    def __init__(self, _app_state) -> None:
        pass

    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def execute(_script, *, args, data_file, output_path) -> tuple[int, str, str]:
        Path(output_path).write_text(
            Path(data_file).read_text(encoding="utf-8") + f"|{args[0]}",
            encoding="utf-8",
        )
        return 0, "", ""


class _ShortcutContext:
    def __init__(self) -> None:
        self.payload = {}

    def set_plugin_state(self, _plugin_id, payload) -> None:
        self.payload = dict(payload)

    def get_plugin_state(self, _plugin_id):
        return dict(self.payload)

    @staticmethod
    def add_summary_line(*_args) -> None:
        pass


def _module():
    name = "_custom_saves_folders_for_test"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _context(game_id: str, config: dict):
    app_state = SimpleNamespace(
        local_config=config,
        game_mode=SimpleNamespace(game_id=game_id),
    )
    return SimpleNamespace(
        app_state=app_state,
        feedback_service=_Feedback(),
        localization_service=_Localization(),
        plugin_settings=_PluginSettings(),
        game_registry_service=None,
        profile_service=None,
    )


def _mod_config(game_id: str, file_entry: dict) -> dict:
    return {
        "config_version": "2.0.0",
        "id": "selected_mod",
        "name": "Selected Mod",
        "version": "1.0.0",
        "authors": ["Test"],
        "game": game_id,
        "files": [file_entry],
    }


@pytest.mark.parametrize(
    ("game_id", "content_dir", "archive"),
    [
        ("frickbears3", "addons", False),
        ("pizzatower", "towers", False),
        ("frickbears3", "addons", True),
        ("frickbears3", "addons", "unreadable"),
    ],
)
def test_custom_folder_migrates_selected_data_files_and_restores(
    tmp_path, monkeypatch, game_id, content_dir, archive
):
    module = _module()
    mods_root = tmp_path / "mods"
    mod_root = mods_root / "selected_mod"
    source_file = mod_root / content_dir / "Pack" / "content.txt"
    source_file.parent.mkdir(parents=True)
    entry_path = f"{content_dir}.zip" if archive else f"{content_dir}/"
    if archive:
        with zipfile.ZipFile(mod_root / entry_path, "w") as archive_file:
            archive_file.writestr("Pack/content.txt", "mod content")
    else:
        source_file.write_text("mod content", encoding="utf-8")
    (mod_root / "mod_config.json").write_text(
        json.dumps(
            _mod_config(
                game_id,
                {
                    "source": f"${{mod_path}}/{entry_path}" + ("/Pack/" if archive == "unreadable" else ""),
                    "target": "${game_data_path}/",
                    "type": "extract" if archive and archive != "unreadable" else "overwrite",
                },
            )
        ),
        encoding="utf-8",
    )
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    data_file = game_dir / "data.win"
    data_file.write_text("original", encoding="utf-8")
    data_dir = tmp_path / "game_data"
    deployed_relative = (
        Path("Pack") / "content.txt"
        if archive
        else Path(content_dir) / "Pack" / "content.txt"
    )
    deployed_file = data_dir / deployed_relative
    deployed_file.parent.mkdir(parents=True)
    deployed_file.write_text("mod content", encoding="utf-8")
    game = SimpleNamespace(
        display_label=game_id,
        get_data_path=lambda _config: str(data_dir),
    )
    context = _context(
        game_id, {f"used_mods_{game_id}": {game_id: ["selected_mod"]}}
    )
    plugin = module.CustomSavesFoldersPlugin()
    plugin.on_load(context)
    assert plugin._state.add_folder(game_id, "", "FB3_CUSTOM") is None
    monkeypatch.setattr(module, "G3MToolManager", _G3MTool)
    monkeypatch.setattr(module, "get_profile_mods_root", lambda _profile: str(mods_root))
    monkeypatch.setattr(module, "get_user_data_root", lambda: str(tmp_path / "runtime"))
    monkeypatch.setattr(
        plugin,
        "_resolve_target_files",
        lambda _game_id: (game, str(game_dir), [str(data_file)]),
    )

    if archive == "unreadable":
        def fail_materialization(*_args):
            raise OSError("Archive unavailable")
        monkeypatch.setattr(module, "materialize_archive", fail_materialization)
        ok, error = plugin._apply_name_to_targets(
            game_id, "FB3_CUSTOM", selections={game_id: [{"id": "selected_mod"}]},
        )
        assert not ok
        assert "Archive unavailable" in error
        assert data_file.read_text(encoding="utf-8") == "original"
        assert plugin._active_session is None
        assert not (data_dir.parent / "FB3_CUSTOM" / "Pack" / "content.txt").exists()
        return

    assert plugin.on_after_mod_apply_before_launch(
        context, {game_id: [{"id": "selected_mod"}]}
    )
    custom_file = data_dir.parent / "FB3_CUSTOM" / deployed_file.relative_to(data_dir)
    assert custom_file.read_text(encoding="utf-8") == "mod content"
    assert deployed_file.read_text(encoding="utf-8") == "mod content"
    assert data_file.read_text(encoding="utf-8") == "original|FB3_CUSTOM"

    assert plugin.on_before_restore_after_exit(context) is True
    assert data_file.read_text(encoding="utf-8") == "original"
    assert not custom_file.exists()
    assert custom_file.parents[2].is_dir()

    shortcut_context = _ShortcutContext()
    assert plugin.on_shortcut_dialog(context, shortcut_context)
    assert shortcut_context.payload["mod_ids"] == ["selected_mod"]
    assert plugin.on_after_mod_apply_before_launch_shortcut(context, shortcut_context)
    assert custom_file.read_text(encoding="utf-8") == "mod content"
    assert plugin.on_before_restore_after_exit(context) is True
    assert not custom_file.exists()


@pytest.mark.parametrize(
    ("file_type", "expected"), [("install", True), ("data", False)],
)
def test_custom_folder_requires_a_data_path_only_for_data_files(
    tmp_path, monkeypatch, file_type, expected
):
    module = _module()
    mods_root = tmp_path / "mods"
    mod_root = mods_root / "selected_mod"
    mod_root.mkdir(parents=True)
    (mod_root / "mod_config.json").write_text(
        json.dumps(
            _mod_config(
                "frickbears3",
                {
                    "source": "${mod_path}/addons/" if file_type == "data" else "${mod_path}/docs/",
                    "target": "${game_data_path}/" if file_type == "data" else "${game_path}/docs/",
                    "type": "overwrite",
                },
            )
        ),
        encoding="utf-8",
    )
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    data_file = game_dir / "data.win"
    data_file.write_text("original", encoding="utf-8")
    game = SimpleNamespace(display_label="FRICKBEARS3", get_data_path=lambda _config: "")
    context = _context("frickbears3", {})
    plugin = module.CustomSavesFoldersPlugin()
    plugin.on_load(context)
    monkeypatch.setattr(module, "G3MToolManager", _G3MTool)
    monkeypatch.setattr(module, "get_profile_mods_root", lambda _profile: str(mods_root))
    monkeypatch.setattr(module, "get_user_data_root", lambda: str(tmp_path / "runtime"))
    monkeypatch.setattr(
        plugin,
        "_resolve_target_files",
        lambda _game_id: (game, str(game_dir), [str(data_file)]),
    )

    ok, _error = plugin._apply_name_to_targets(
        "frickbears3",
        "FB3_CUSTOM",
        selections={"frickbears3": [{"id": "selected_mod"}]},
    )

    assert ok is expected
    assert data_file.read_text(encoding="utf-8") == (
        "original|FB3_CUSTOM" if expected else "original"
    )


def test_custom_folder_rejects_destination_through_existing_symlink(
    tmp_path, monkeypatch
):
    module = _module()
    data_dir = tmp_path / "game_data"
    data_dir.mkdir()
    deployed_file = data_dir / "addons" / "Guard" / "content.txt"
    deployed_file.parent.mkdir(parents=True)
    deployed_file.write_text("mod content", encoding="utf-8")
    custom_data_dir = data_dir.parent / "FB3_CUSTOM"
    custom_data_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    try:
        (custom_data_dir / "addons").symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    game = SimpleNamespace(get_data_path=lambda _config: str(data_dir))
    plugin = module.CustomSavesFoldersPlugin()
    plugin._context = _context("frickbears3", {})
    mod_root = tmp_path / "mod"
    (mod_root / "addons" / "Guard").mkdir(parents=True)
    (mod_root / "addons" / "Guard" / "content.txt").write_text(
        "mod content", encoding="utf-8"
    )
    config = _mod_config(
        "frickbears3",
        {
            "source": "${mod_path}/addons/",
            "target": "${game_data_path}/",
            "type": "overwrite",
        },
    )
    monkeypatch.setattr(
        plugin, "_selected_mod_configs", lambda *_args: [(str(mod_root), config)]
    )

    ok, error = plugin._migrate_selected_data_files(
        game,
        "frickbears3",
        "FB3_CUSTOM",
        {},
        SimpleNamespace(backup_file=lambda *_args: (_ for _ in ()).throw(AssertionError)),
    )

    assert ok is False
    assert error == "errors.custom_data_folder_unsafe"
    assert not (outside_dir / "Guard" / "content.txt").exists()


def test_custom_folder_copies_only_files_deployed_by_directory_operation(
    tmp_path, monkeypatch
):
    module = _module()
    data_dir = tmp_path / "game_data"
    data_dir.mkdir()
    mod_root = tmp_path / "mod"
    source_file = mod_root / "Pack" / "content.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("mod content", encoding="utf-8")
    deployed_file = data_dir / "addons" / "Pack" / "content.txt"
    deployed_file.parent.mkdir(parents=True)
    deployed_file.write_text("mod content", encoding="utf-8")
    unrelated_file = deployed_file.parent / "untouched.txt"
    unrelated_file.write_text("game content", encoding="utf-8")
    game = SimpleNamespace(get_data_path=lambda _config: str(data_dir))
    plugin = module.CustomSavesFoldersPlugin()
    plugin._context = _context("frickbears3", {})
    config = _mod_config(
        "frickbears3",
        {
            "source": "${mod_path}/Pack/",
            "target": "${addons_path}/",
            "type": "soft-overwrite",
        },
    )
    config["placeholders"] = {"addons_path": "${game_data_path}/addons"}
    monkeypatch.setattr(
        plugin, "_selected_mod_configs", lambda *_args: [(str(mod_root), config)]
    )

    ok, error = plugin._migrate_selected_data_files(
        game,
        "frickbears3",
        "FB3_CUSTOM",
        {},
        SimpleNamespace(backup_file=lambda *_args: True),
    )

    assert ok is True
    assert error == ""
    custom_data_dir = data_dir.parent / "FB3_CUSTOM" / "addons" / "Pack"
    assert (custom_data_dir / "content.txt").read_text(encoding="utf-8") == "mod content"
    assert not (custom_data_dir / "untouched.txt").exists()


def test_custom_folder_copies_permitted_absolute_data_target(tmp_path, monkeypatch):
    module = _module()
    data_dir = tmp_path / "game_data"
    data_dir.mkdir()
    mod_root = tmp_path / "mod"
    source_file = mod_root / "content.txt"
    source_file.parent.mkdir()
    source_file.write_text("mod content", encoding="utf-8")
    deployed_file = data_dir / "addons" / "content.txt"
    deployed_file.parent.mkdir()
    deployed_file.write_text("mod content", encoding="utf-8")
    game = SimpleNamespace(get_data_path=lambda _config: str(data_dir))
    plugin = module.CustomSavesFoldersPlugin()
    plugin._context = _context("frickbears3", {})
    config = _mod_config(
        "frickbears3",
        {
            "source": "${mod_path}/content.txt",
            "target": deployed_file.as_posix(),
            "type": "overwrite",
        },
    )
    monkeypatch.setattr(
        plugin, "_selected_mod_configs", lambda *_args: [(str(mod_root), config)]
    )

    ok, error = plugin._migrate_selected_data_files(
        game,
        "frickbears3",
        "FB3_CUSTOM",
        {},
        SimpleNamespace(backup_file=lambda *_args: True),
    )

    assert ok is True
    assert error == ""
    assert (
        data_dir.parent / "FB3_CUSTOM" / "addons" / "content.txt"
    ).read_text(encoding="utf-8") == "mod content"


def test_custom_folder_skips_an_untouched_soft_overwrite(tmp_path, monkeypatch):
    module = _module()
    data_dir = tmp_path / "game_data"
    data_dir.mkdir()
    mod_root = tmp_path / "mod"
    source_file = mod_root / "content.txt"
    source_file.parent.mkdir()
    source_file.write_text("mod content", encoding="utf-8")
    deployed_file = data_dir / "content.txt"
    deployed_file.write_text("game content", encoding="utf-8")
    game = SimpleNamespace(get_data_path=lambda _config: str(data_dir))
    plugin = module.CustomSavesFoldersPlugin()
    plugin._context = _context("frickbears3", {})
    config = _mod_config(
        "frickbears3",
        {
            "source": "${mod_path}/content.txt",
            "target": "${game_data_path}/content.txt",
            "type": "soft-overwrite",
        },
    )
    monkeypatch.setattr(
        plugin, "_selected_mod_configs", lambda *_args: [(str(mod_root), config)]
    )

    ok, error = plugin._migrate_selected_data_files(
        game,
        "frickbears3",
        "FB3_CUSTOM",
        {},
        SimpleNamespace(backup_file=lambda *_args: True),
    )

    assert ok is True
    assert error == ""
    assert not (data_dir.parent / "FB3_CUSTOM" / "content.txt").exists()
