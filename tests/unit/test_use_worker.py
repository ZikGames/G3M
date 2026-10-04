"""Unit tests for the Downloads Use worker."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from helpers import FailingSignal

from models.download_models import TargetKind
from workers.install.helpers_install import find_mod_config
from workers.use_worker import UseWorker


@pytest.mark.parametrize("case", ["normal", "same_file", "cancelled", "unsafe_id", "copy_failure"])
def test_theme_install_preserves_existing_files_on_failure(tmp_path, monkeypatch, case):
    themes = tmp_path / "themes"
    themes.mkdir()
    destination = themes / "sample.zip"
    source = destination if case == "same_file" else tmp_path / "sample.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("theme_config.json", "{}")
    payload = source.read_bytes()
    if source != destination:
        destination.write_bytes(b"existing theme")
    worker = UseWorker(
        "theme", str(source), TargetKind.THEME, "",
        {"theme_id": "../outside"} if case == "unsafe_id" else {},
        themes_dir=str(themes),
    )
    finished = []
    worker.use_finished.connect(lambda *args: finished.append(args))
    if case == "cancelled":
        worker.cancel()
    elif case == "copy_failure":
        def fail_copy(_source, staged):
            Path(staged).write_bytes(b"incomplete")
            raise OSError("Disk full")
        monkeypatch.setattr("workers.use_worker.shutil.copy2", fail_copy)

    worker.run()

    success = case in {"normal", "same_file"}
    assert len(finished) == 1
    assert finished[0][1] is success
    assert destination.read_bytes() == (payload if success else b"existing theme")
    assert sorted(path.name for path in themes.iterdir()) == ["sample.zip"]
    assert not (tmp_path / "outside.zip").exists()
    if not success or case == "same_file":
        assert source.read_bytes() == payload


def test_failed_snapshot_prevents_mod_update(tmp_path, monkeypatch):
    current = tmp_path / "mods" / "current"
    current.mkdir(parents=True)
    (current / "mod_config.json").write_text(json.dumps({"id": "gb_mod_123", "name": "Current"}), encoding="utf-8")
    old = current / "old.txt"
    old.write_text("previous version", encoding="utf-8")
    source = tmp_path / "incoming.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("mod_config.json", json.dumps({"id": "gb_mod_123", "name": "Updated"}))
        archive.writestr("new.txt", "new version")
    monkeypatch.setattr("utils.mod.version_utils.create_version_zip", Mock(side_effect=OSError("Disk full")))
    worker = UseWorker(
        "update", str(source), TargetKind.MOD, str(current.parent),
        {"snapshot_version": "1.0.0", "snapshot_mod_folder": str(current), "update_mod_id": "gb_mod_123"},
    )
    finished = []
    worker.use_finished.connect(lambda *args: finished.append(args))

    worker.run()

    assert len(finished) == 1
    assert finished[0][1] is False
    assert old.read_text(encoding="utf-8") == "previous version"
    assert not (current / "new.txt").exists()
    assert source.is_file()


def test_failed_version_write_preserves_previous_archive(tmp_path, monkeypatch):
    from utils.mod.version_utils import create_version_zip

    mod = tmp_path / "mod"
    versions = mod / "mod_versions"
    versions.mkdir(parents=True)
    (mod / "payload.txt").write_text("payload", encoding="utf-8")
    previous = versions / "1.0.0.zip"
    previous.write_bytes(b"previous archive")
    monkeypatch.setattr(zipfile.ZipFile, "write", Mock(side_effect=OSError("Disk full")))

    with pytest.raises(OSError, match="Disk full"):
        create_version_zip(str(mod), str(mod), "1.0.0", ignore_versions_dir=True)

    assert previous.read_bytes() == b"previous archive"
    assert list(versions.iterdir()) == [previous]


def test_raw_gamebanana_archive_requires_setup_instead_of_fake_install(tmp_path):
    archive_path = tmp_path / "multiplayer.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("1.0 Prerelease.xdelta", b"patch")
        archive.writestr("data.win", b"replacement")
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    worker = UseWorker(
        record_id="wip_84933",
        file_path=str(archive_path),
        target_kind=TargetKind.MOD,
        mods_dir=str(mods_dir),
        metadata={
            "gb_mod_id": 84933,
            "item_type": "wip",
            "name": "DELTARUNE Multiplayer Mod!",
            "game": "deltarune",
        },
    )
    finished = []
    worker.use_finished.connect(lambda *args: finished.append(args))

    worker.run()

    assert finished == [("wip_84933", False, True, "")]
    assert list(mods_dir.iterdir()) == []


def test_find_mod_config_requires_the_unwrapped_package_root(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    direct = root / "mod_config.json"
    direct.write_text("{}", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "mod_config.json").write_text("{}", encoding="utf-8")

    assert find_mod_config(str(root)) == str(direct)

    direct.unlink()

    assert find_mod_config(str(root)) is None


def test_plugin_use_does_not_delete_successfully_installed_plugin_when_cancelled_late(
    temp_dir,
):
    """Checks that late cancellation cannot erase an already installed plugin update."""
    archive_path = os.path.join(temp_dir, "plugin.zip")
    with open(archive_path, "wb") as handle:
        handle.write(b"plugin archive")
    install_service = Mock()
    worker = UseWorker(
        record_id="record_1",
        file_path=archive_path,
        target_kind=TargetKind.PLUGIN,
        mods_dir="",
        metadata={"source": "catalog", "plugin_id": "sample_plugin"},
        plugin_install_service=install_service,
    )

    def cancel_then_install(*_args, **_kwargs):
        worker.cancel()
        return "sample_plugin"

    install_service.install_archive.side_effect = cancel_then_install
    finished = []
    worker.use_finished.connect(lambda *args: finished.append(args))

    worker.run()

    install_service.delete_plugin.assert_not_called()
    assert finished == [("record_1", True, False, "")]


def test_plugin_use_suppresses_emit_failure_after_install_error(temp_dir, caplog):
    """Checks that plugin install failures cannot crash while notifying a dead UI."""
    archive_path = os.path.join(temp_dir, "plugin.zip")
    with open(archive_path, "wb") as handle:
        handle.write(b"plugin archive")
    install_service = Mock()
    install_service.install_archive.side_effect = RuntimeError("install failed")
    worker = UseWorker(
        record_id="record_1",
        file_path=archive_path,
        target_kind=TargetKind.PLUGIN,
        mods_dir="",
        metadata={"source": "catalog", "plugin_id": "sample_plugin"},
        plugin_install_service=install_service,
    )

    vars(worker)["use_finished"] = FailingSignal()

    worker.run()

    assert "UseWorker: plugin install failed" in caplog.text
    assert "UseWorker: failed to emit" in caplog.text


def test_g3m_update_replaces_atomically_and_keeps_versions(tmp_path):
    mods_dir = tmp_path / "mods"
    current_mod = mods_dir / "Current Mod"
    versions_dir = current_mod / "mod_versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "1.0.0.zip").write_bytes(b"previous")
    (current_mod / "old.txt").write_text("old", encoding="utf-8")
    (current_mod / "mod_config.json").write_text(
        json.dumps({"id": "gb_mod_123", "name": "Current Mod"}), encoding="utf-8"
    )
    content_dir = tmp_path / "incoming"
    content_dir.mkdir()
    (content_dir / "new.txt").write_text("new", encoding="utf-8")
    (content_dir / "mod_config.json").write_text(
        json.dumps(
            {
                "config_version": "2.0.0",
                "id": "incoming",
                "name": "Updated Mod",
                "version": "2.0.0",
                "authors": ["Author"],
                "game": "deltarune",
                "files": [],
            }
        ),
        encoding="utf-8",
    )

    worker = UseWorker(
        record_id="update",
        file_path="",
        target_kind=TargetKind.MOD,
        mods_dir=str(mods_dir),
        metadata={},
    )

    assert worker._install_g3m_mod(
        str(content_dir), {"mod_id": 123, "item_type": "mod", "version": "2.0.0"}
    )
    assert (current_mod / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (current_mod / "old.txt").exists()
    assert (versions_dir / "1.0.0.zip").read_bytes() == b"previous"
    assert json.loads((current_mod / "mod_config.json").read_text(encoding="utf-8"))["id"] == "gb_mod_123"


def test_g3m_update_restores_previous_mod_when_publish_fails(tmp_path, monkeypatch):
    mods_dir = tmp_path / "mods"
    current_mod = mods_dir / "Current Mod"
    current_mod.mkdir(parents=True)
    (current_mod / "old.txt").write_text("old", encoding="utf-8")
    (current_mod / "mod_config.json").write_text(
        json.dumps({"id": "gb_mod_123", "name": "Current Mod"}), encoding="utf-8"
    )
    content_dir = tmp_path / "incoming"
    content_dir.mkdir()
    (content_dir / "mod_config.json").write_text(
        json.dumps(
            {
                "config_version": "2.0.0",
                "id": "incoming",
                "name": "Updated Mod",
                "version": "2.0.0",
                "authors": ["Author"],
                "game": "deltarune",
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    real_replace = os.replace

    def fail_publish(source, destination):
        if str(source).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] == "mod" and str(destination) == str(current_mod):
            raise OSError("simulated publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr("workers.use_worker.os.replace", fail_publish)
    worker = UseWorker(
        record_id="update",
        file_path="",
        target_kind=TargetKind.MOD,
        mods_dir=str(mods_dir),
        metadata={},
    )

    assert not worker._install_g3m_mod(
        str(content_dir), {"mod_id": 123, "item_type": "mod"}
    )
    assert (current_mod / "old.txt").read_text(encoding="utf-8") == "old"


def test_g3m_update_keeps_backup_when_restore_also_fails(tmp_path, monkeypatch):
    mods_dir = tmp_path / "mods"
    current_mod = mods_dir / "Current Mod"
    current_mod.mkdir(parents=True)
    (current_mod / "old.txt").write_text("old", encoding="utf-8")
    (current_mod / "mod_config.json").write_text(
        json.dumps({"id": "gb_mod_123", "name": "Current Mod"}), encoding="utf-8"
    )
    content_dir = tmp_path / "incoming"
    content_dir.mkdir()
    (content_dir / "mod_config.json").write_text(
        json.dumps(
            {
                "config_version": "2.0.0",
                "id": "incoming",
                "name": "Updated Mod",
                "version": "2.0.0",
                "authors": ["Author"],
                "game": "deltarune",
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    real_replace = os.replace

    def fail_publish_and_restore(source, destination):
        if str(destination) == str(current_mod):
            raise OSError("simulated replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr("workers.use_worker.os.replace", fail_publish_and_restore)
    worker = UseWorker(
        record_id="update",
        file_path="",
        target_kind=TargetKind.MOD,
        mods_dir=str(mods_dir),
        metadata={},
    )

    assert not worker._install_g3m_mod(
        str(content_dir), {"mod_id": 123, "item_type": "mod"}
    )
    staging_roots = list(mods_dir.glob(".g3m-install-*"))
    assert len(staging_roots) == 1
    assert (staging_roots[0] / "previous" / "old.txt").read_text(encoding="utf-8") == "old"
