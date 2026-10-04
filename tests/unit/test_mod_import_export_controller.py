"""Regression tests for local mod imports."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from controllers.mod.import_export_controller import ModImportExportController
from utils.mod.config import MOD_CONFIG_MAX_BYTES


@pytest.mark.parametrize(
    "raw",
    [
        b'{"config_version":"2.0.0","id":"first","id":"second"}',
        b" " * (MOD_CONFIG_MAX_BYTES + 1),
    ],
    ids=["duplicate_key", "oversized"],
)
def test_local_import_rejects_invalid_config_without_changing_source(tmp_path, raw):
    source = tmp_path / "source"
    source.mkdir()
    config_path = source / "mod_config.json"
    config_path.write_bytes(raw)
    mods_dir = tmp_path / "mods"
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(mods_dir)),
        SimpleNamespace(get_mod_folder_path=Mock(return_value=None)),
        SimpleNamespace(),
    )
    controller._refresh_mod_list = Mock()
    controller._show_import_error_with_manual_install = Mock()

    controller._install_mod_from_file(str(source))

    assert config_path.read_bytes() == raw
    assert not mods_dir.exists()
    controller.mod_service.get_mod_folder_path.assert_not_called()
    controller._refresh_mod_list.assert_not_called()
    controller._show_import_error_with_manual_install.assert_called_once()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"config_version":"2.0.0","id":"first","id":"second"}',
        b" " * (MOD_CONFIG_MAX_BYTES + 1),
    ],
    ids=["duplicate_key", "oversized"],
)
def test_mod_editor_rejects_invalid_managed_config_without_rewriting(tmp_path, monkeypatch, raw):
    folder = tmp_path / "mod"
    folder.mkdir()
    config_path = folder / "mod_config.json"
    config_path.write_bytes(raw)
    editor = Mock()
    monkeypatch.setattr("ui.dialogs.mod_editor.dialog.ModEditorDialog", editor)
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(tmp_path)),
        SimpleNamespace(get_mod_folder_path=lambda _mod_id: str(folder)),
        SimpleNamespace(),
    )
    controller._safe_show_critical = Mock()

    controller.show_mod_details_dialog(SimpleNamespace(id="mod"))

    assert config_path.read_bytes() == raw
    editor.assert_not_called()
    controller._safe_show_critical.assert_called_once()


@pytest.mark.parametrize("finder", ["_find_mod_dir_by_id", "_find_mod_dir_by_config"])
def test_mod_directory_lookup_rejects_duplicate_config_keys(tmp_path, finder):
    folder = tmp_path / "mod"
    folder.mkdir()
    raw = b'{"id":"first","id":"second","name":"Mod","files":{}}'
    config_path = folder / "mod_config.json"
    config_path.write_bytes(raw)
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(tmp_path)), SimpleNamespace(), SimpleNamespace(),
    )
    requested = "second" if finder == "_find_mod_dir_by_id" else SimpleNamespace(id="second", name="Mod")

    assert getattr(controller, finder)(requested) is None
    assert config_path.read_bytes() == raw


@pytest.mark.parametrize("finder", ["_find_mod_dir_by_id", "_find_mod_dir_by_config"])
def test_mod_directory_lookup_reads_legacy_metadata_ids_without_rewriting(tmp_path, finder):
    folder = tmp_path / "mod"
    folder.mkdir()
    config_path = folder / "mod_config.json"
    raw = json.dumps({"metadata": {"id": "nested_id", "name": "Legacy"}, "files": {}})
    config_path.write_text(raw, encoding="utf-8")
    controller = ModImportExportController(SimpleNamespace(mods_dir=str(tmp_path)), SimpleNamespace(), SimpleNamespace())
    requested = "nested_id" if finder == "_find_mod_dir_by_id" else SimpleNamespace(id="nested_id", name="Legacy")
    assert getattr(controller, finder)(requested) == str(folder)
    assert config_path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("filename", ["export.zip", "mod_config.json"])
def test_export_rejects_destination_inside_mod_before_opening_it(tmp_path, filename):
    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    config_path = mod_dir / "mod_config.json"
    config_path.write_text('{"id": "mod"}', encoding="utf-8")
    controller = ModImportExportController(
        SimpleNamespace(),
        SimpleNamespace(get_mod_folder_path=lambda _mod_id: str(mod_dir)),
        SimpleNamespace(),
    )

    assert not controller.export_mod_to_path(SimpleNamespace(id="mod"), str(mod_dir / filename))
    assert config_path.read_text(encoding="utf-8") == '{"id": "mod"}'
    assert not (mod_dir / "export.zip").exists()


def test_local_import_migrates_legacy_config_before_copying(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "mod_config.json").write_text(
        json.dumps(
            {
                "config_version": "1.0.0",
                "metadata": {
                    "name": "Imported Mod",
                    "version": "1.0.0",
                    "author": "Author",
                    "game": "deltarune",
                },
                "files": {
                    "deltarune_1": {"data_file_path": "data.win"},
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "data.win").write_bytes(b"patch")
    (source / "icon.png").write_bytes(b"icon")
    mods_dir = tmp_path / "mods"
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(mods_dir)),
        SimpleNamespace(get_mod_folder_path=lambda _mod_id: None),
        SimpleNamespace(),
    )
    vars(controller)["_materialize_local_import"] = lambda _path, _temp: str(source)
    controller._refresh_mod_list = lambda: None
    vars(controller)["_safe_show_information"] = lambda *_args: None
    vars(controller)["_safe_show_critical"] = lambda *_args: None
    vars(controller)["_show_import_error_with_manual_install"] = lambda *_args: None

    controller._install_mod_from_file(str(tmp_path / "import.zip"))

    imported = json.loads((mods_dir / "Imported Mod" / "mod_config.json").read_text())
    assert imported["config_version"] == "2.0.0"
    assert imported["id"].startswith("local_")
    assert imported["icon"] == "${mod_path}/icon.png"
    assert imported["files"] == [
        {
            "source": "${mod_path}/data.win",
            "type": "patch",
            "target": "${game_path}/chapter1_windows/data.win",
        }
    ]


def test_local_import_can_be_cancelled_for_direct_absolute_paths(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "mod_config.json").write_text(
        json.dumps(
            {
                "config_version": "2.0.0",
                "id": "direct_path_mod",
                "name": "Direct Path Mod",
                "version": "1.0.0",
                "authors": [],
                "game": "deltarune",
                "files": [
                    {
                        "source": (source / "payload.bin").as_posix(),
                        "target": (tmp_path / "outside.bin").as_posix(),
                        "type": "overwrite",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    feedback = Mock()
    feedback.ask_patching_warning.return_value = False
    mods_dir = tmp_path / "mods"
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(mods_dir), local_config={}),
        SimpleNamespace(get_mod_folder_path=lambda _mod_id: None),
        SimpleNamespace(feedback_service=feedback),
    )
    vars(controller)["_materialize_local_import"] = lambda _path, _temp: str(source)
    vars(controller)["_show_import_error_with_manual_install"] = lambda *_args: None

    controller._install_mod_from_file(str(tmp_path / "import.zip"))

    assert not mods_dir.exists()
    event = feedback.ask_patching_warning.call_args.args[0]
    assert event.warning_id == "direct_absolute_operation_paths"


def test_opening_legacy_mod_editor_migrates_its_managed_config(tmp_path, monkeypatch):
    mod_folder = tmp_path / "legacy"
    mod_folder.mkdir()
    config_path = mod_folder / "mod_config.json"
    config_path.write_text(
        json.dumps(
            {
                "config_version": "1.0.0",
                "id": "legacy_mod",
                "name": "Legacy Mod",
                "version": "1.0.0",
                "author": "Author",
                "game": "deltarune",
                "files": {"deltarune_1": {"data_file_path": "DATA.win"}},
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    class _Editor:
        def __init__(self, _parent, *, is_creating, mod_data) -> None:
            captured.update(is_creating=is_creating, mod_data=mod_data)

        def exec(self):
            return 0

    monkeypatch.setattr("ui.dialogs.mod_editor.dialog.ModEditorDialog", _Editor)
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(tmp_path)),
        SimpleNamespace(get_mod_folder_path=lambda _mod_id: str(mod_folder)),
        SimpleNamespace(),
    )

    controller.show_mod_details_dialog(SimpleNamespace(id="legacy_mod"))

    assert captured["is_creating"] is False
    assert captured["mod_data"]["config_version"] == "2.0.0"
    assert json.loads(config_path.read_text(encoding="utf-8"))["config_version"] == "2.0.0"


def test_mixed_imports_install_known_mods_before_one_manual_batch(tmp_path, monkeypatch):
    controller = ModImportExportController(
        SimpleNamespace(mods_dir=str(tmp_path / "mods")),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    automatic = ["ready-one.zip", "ready-two.zip"]
    manual = ["patch.xdelta", "readme.png"]
    events = []
    monkeypatch.setattr(
        controller, "_is_automatic_mod_source", lambda path: path in automatic
    )
    monkeypatch.setattr(
        controller,
        "_install_mod_from_file",
        lambda path: events.append(("automatic", path)),
    )
    monkeypatch.setattr(
        controller,
        "_show_manual_import_batch",
        lambda paths: events.append(("manual", paths)),
    )
    monkeypatch.setattr(
        "controllers.mod.import_export_controller.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    controller.import_files_sequentially(
        [automatic[0], manual[0], automatic[1], manual[1]]
    )

    assert events == [
        ("automatic", automatic[0]),
        ("automatic", automatic[1]),
        ("manual", manual),
    ]
    assert controller._importing is False
    assert controller._import_queue == []
    assert controller._manual_import_batches == []
