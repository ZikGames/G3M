"""Tests for migration of G3M-owned mod copies and version snapshots."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile

import pytest

from config.config import MOD_CONFIG_FILENAME, MOD_VERSIONS_DIR
from services.mod_config_migration_service import migrate_managed_mods
from utils.mod.config import MOD_CONFIG_VERSION


def _legacy_config() -> dict[str, object]:
    return {
        "id": "legacy_mod",
        "name": "Legacy Mod",
        "author": "Legacy Author",
        "game": "deltarune",
        "files": {
            "deltarune_1": {
                "extra_files": [{"file_path": "chapter_1/lang", "target": "game_folder"}],
            }
        },
    }


def test_migrate_managed_mods_migrates_current_copy_and_snapshot(tmp_path):
    mod_folder = tmp_path / "legacy"
    mod_folder.mkdir()
    config_path = mod_folder / MOD_CONFIG_FILENAME
    config_path.write_text(json.dumps(_legacy_config()), encoding="utf-8")
    versions = mod_folder / MOD_VERSIONS_DIR
    versions.mkdir()
    snapshot_path = versions / "before.zip"
    with zipfile.ZipFile(snapshot_path, "w") as snapshot:
        snapshot.writestr(MOD_CONFIG_FILENAME, json.dumps(_legacy_config()))
        snapshot.writestr("chapter_1/lang/en.txt", "English")

    report = migrate_managed_mods(tmp_path)

    assert report.configs == (config_path,)
    assert report.issues == ()
    assert report.snapshots == (snapshot_path,)
    assert json.loads(config_path.read_text(encoding="utf-8"))["config_version"] == MOD_CONFIG_VERSION
    with zipfile.ZipFile(snapshot_path) as snapshot:
        migrated = json.loads(snapshot.read(MOD_CONFIG_FILENAME))
        assert migrated["files"] == [
            {
                "source": "${mod_path}/chapter_1/lang/",
                "target": "${game_path}/chapter1_windows/lang/",
                "type": "extract",
            }
        ]
        assert snapshot.read("chapter_1/lang/en.txt") == b"English"


def test_migrate_managed_mods_reports_bad_snapshot_without_replacing_it(tmp_path):
    versions = tmp_path / "legacy" / MOD_VERSIONS_DIR
    versions.mkdir(parents=True)
    snapshot_path = versions / "bad.zip"
    with zipfile.ZipFile(snapshot_path, "w") as snapshot:
        snapshot.writestr(MOD_CONFIG_FILENAME, "not json")
    original = snapshot_path.read_bytes()

    report = migrate_managed_mods(tmp_path)

    assert report.snapshots == ()
    assert report.issues[0].path == snapshot_path
    assert snapshot_path.read_bytes() == original


def test_migrate_managed_mods_keeps_a_failed_config_for_diagnostics(tmp_path):
    mod_folder = tmp_path / "broken"
    mod_folder.mkdir()
    config_path = mod_folder / MOD_CONFIG_FILENAME
    config_path.write_text("not json", encoding="utf-8")

    report = migrate_managed_mods(tmp_path)

    assert report.configs == ()
    assert report.issues[0].path == config_path
    assert config_path.read_text(encoding="utf-8") == "not json"


@pytest.mark.parametrize(("mod_id", "game"), [("Legacy Mod", "deltarune"), ("legacy_mod", "Unknown Game")])
def test_managed_migration_preserves_identity_or_reports_invalid_metadata(tmp_path, mod_id, game):
    mod = tmp_path / "legacy"
    mod.mkdir()
    path = mod / MOD_CONFIG_FILENAME
    config = {**_legacy_config(), "id": mod_id, "game": game}
    path.write_text(json.dumps(config), encoding="utf-8")
    original = path.read_bytes()

    report = migrate_managed_mods(tmp_path)

    assert report.configs == ()
    assert report.issues[0].path == path
    assert path.read_bytes() == original


def test_migrate_managed_mods_leaves_valid_operation_files_unchanged(tmp_path):
    mod_folder = tmp_path / "ready"
    mod_folder.mkdir()
    config_path = mod_folder / MOD_CONFIG_FILENAME
    config_path.write_text(
        json.dumps(
            {
                "config_version": "2.0.0",
                "id": "ready_mod",
                "name": "Ready",
                "version": "1.0.0",
                "authors": [],
                "game": "deltarune",
                "files": [],
            },
            indent=4,
        ),
        encoding="utf-8",
    )
    original = config_path.read_bytes()

    report = migrate_managed_mods(tmp_path)

    assert report.configs == ()
    assert report.snapshots == ()
    assert report.issues == ()
    assert config_path.read_bytes() == original


def test_migrate_managed_mods_records_legacy_root_icon_in_current_config(tmp_path):
    mod_folder = tmp_path / "legacy-icon"
    mod_folder.mkdir()
    config_path = mod_folder / MOD_CONFIG_FILENAME
    config_path.write_text(
        json.dumps(
            {
                "config_version": "2.0.0",
                "id": "legacy_icon",
                "name": "Legacy icon",
                "version": "1.0.0",
                "authors": [],
                "game": "deltarune",
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    (mod_folder / "_icon.png").write_bytes(b"icon")

    report = migrate_managed_mods(tmp_path)

    assert report.configs == (config_path,)
    assert json.loads(config_path.read_text(encoding="utf-8"))["icon"] == "${mod_path}/_icon.png"


def test_migrate_managed_mods_rewrites_tar_snapshot(tmp_path):
    versions = tmp_path / "legacy" / MOD_VERSIONS_DIR
    versions.mkdir(parents=True)
    snapshot_path = versions / "before.tar.gz"
    with tarfile.open(snapshot_path, "w:gz") as snapshot:
        config = json.dumps(_legacy_config()).encode("utf-8")
        config_info = tarfile.TarInfo(MOD_CONFIG_FILENAME)
        config_info.size = len(config)
        snapshot.addfile(config_info, io.BytesIO(config))
        contents = b"English"
        file_info = tarfile.TarInfo("chapter_1/lang/en.txt")
        file_info.size = len(contents)
        snapshot.addfile(file_info, io.BytesIO(contents))

    report = migrate_managed_mods(tmp_path)

    assert report.snapshots == (snapshot_path,)
    assert report.issues == ()
    with tarfile.open(snapshot_path, "r:gz") as snapshot:
        migrated = json.loads(snapshot.extractfile(MOD_CONFIG_FILENAME).read())  # type: ignore[union-attr]
        assert migrated["config_version"] == MOD_CONFIG_VERSION
        assert snapshot.extractfile("chapter_1/lang/en.txt").read() == b"English"  # type: ignore[union-attr]


@pytest.mark.parametrize("config_version", ["2.0.1", "3.0.0", None])
def test_migration_preserves_unsupported_operation_configs_and_snapshots(tmp_path, config_version):
    mod_folder = tmp_path / "future"
    versions = mod_folder / MOD_VERSIONS_DIR
    versions.mkdir(parents=True)
    config = {
        "id": "future_mod", "name": "Future", "version": "1", "authors": ["Author"],
        "game": "undertale", "files": [{
            "source": "${mod_path}/payload.txt", "target": "${game_path}/payload.txt", "type": "overwrite",
        }],
    }
    if config_version is not None:
        config["config_version"] = config_version
    config_path = mod_folder / MOD_CONFIG_FILENAME
    original_config = json.dumps(config).encode("utf-8")
    config_path.write_bytes(original_config)
    snapshot_path = versions / "future.zip"
    with zipfile.ZipFile(snapshot_path, "w") as snapshot:
        snapshot.writestr(MOD_CONFIG_FILENAME, original_config)
    original_snapshot = snapshot_path.read_bytes()

    report = migrate_managed_mods(tmp_path)

    assert report.configs == ()
    assert report.snapshots == ()
    assert {issue.path for issue in report.issues} == {config_path, snapshot_path}
    assert config_path.read_bytes() == original_config
    assert snapshot_path.read_bytes() == original_snapshot


def test_migration_reports_excessive_depth_without_rewriting_the_config(tmp_path):
    mod_folder = tmp_path / "deep"
    mod_folder.mkdir()
    config_path = mod_folder / MOD_CONFIG_FILENAME
    raw = b'{"metadata":' + b"[" * 2_000 + b"]" * 2_000 + b"}"
    config_path.write_bytes(raw)

    report = migrate_managed_mods(tmp_path)

    assert report.configs == ()
    assert report.issues[0].path == config_path
    assert "nesting depth" in report.issues[0].message
    assert config_path.read_bytes() == raw
