"""Download coordinator terminal-state tests."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from models.download_models import (
    DownloadRecord,
    DownloadStatus,
    SourceKind,
    TargetKind,
    UseStatus,
)
from services.downloads.manager import DownloadsManager


@pytest.mark.parametrize("checksum", ["0" * 32, "invalid-checksum"])
def test_gamebanana_checksum_failure_never_installs_over_existing_mod(tmp_path, monkeypatch, checksum):
    from workers.download_worker import DownloadWorker

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    existing = mods_dir / "working-data.win"
    existing.write_bytes(b"original mod")
    manager = DownloadsManager(str(tmp_path), lambda: {})
    manager.set_app_context(mods_dir=str(mods_dir))
    install = Mock()
    monkeypatch.setattr(manager, "_start_use", install)
    monkeypatch.setattr(DownloadWorker, "start", lambda worker: worker.run())
    monkeypatch.setattr("utils.network_utils.get_session", lambda: object())
    monkeypatch.setattr("utils.network_utils.download_file", lambda _session, _url, path, **_kwargs: Path(path).write_bytes(b"corrupt archive"))

    record_id, _duplicate = manager.enqueue(
        "Update", source_kind=SourceKind.GAMEBANANA,
        source_url="https://gamebanana.com/dl/123", metadata={"md5": checksum}, auto_use=True,
    )

    record = manager.store.find(record_id)
    assert record.download_status == DownloadStatus.FAILED
    assert record.use_status == UseStatus.FAILED
    assert record.file_exists is False
    install.assert_not_called()
    assert existing.read_bytes() == b"original mod"


def test_worker_cleanup_uses_native_safe_retirement(monkeypatch) -> None:
    from services.downloads.manager import _cleanup_worker

    worker = object()
    retired = []
    monkeypatch.setattr(
        "ui.utils.thread_lifetime.retire_qthread", retired.append
    )

    _cleanup_worker(worker)

    assert retired == [worker]


def test_mod_downloads_keep_their_original_profile_and_deduplicate_per_profile(tmp_path, monkeypatch):
    manager = DownloadsManager(str(tmp_path), lambda: {})
    monkeypatch.setattr(manager, "_start_download", lambda _record: None)
    first_dir = str(tmp_path / "first")
    second_dir = str(tmp_path / "second")
    manager.set_app_context(mods_dir=first_dir)
    first_id, duplicate = manager.enqueue("Mod", canonical_key="gb_mod_1_2")
    assert not duplicate
    manager.set_app_context(mods_dir=second_dir)
    second_id, duplicate = manager.enqueue("Mod", canonical_key="gb_mod_1_2")
    assert not duplicate
    assert second_id != first_id
    assert manager._mod_target_dir(manager.store.find(first_id)) == first_dir
    assert manager._mod_target_dir(manager.store.find(second_id)) == second_dir
    assert manager.enqueue("Mod", canonical_key="gb_mod_1_2") == (second_id, True)


def test_legacy_download_uses_the_manager_profile_when_matching_duplicates(tmp_path, monkeypatch):
    manager = DownloadsManager(str(tmp_path), lambda: {})
    monkeypatch.setattr(manager, "_start_download", lambda _record: None)
    manager.set_app_context(mods_dir=str(tmp_path / "active"))
    legacy = DownloadRecord(id="legacy", display_name="Mod", canonical_key="mod", metadata={})
    manager.store.add(legacy)
    record_id, duplicate = manager.enqueue("Mod", canonical_key="mod", metadata={"target_mods_dir": str(tmp_path / "other")})
    assert not duplicate
    assert record_id != legacy.id
    assert manager.enqueue("Mod", canonical_key="mod") == (legacy.id, True)


def test_catalog_theme_can_be_downloaded_again_after_deletion(tmp_path, monkeypatch):
    manager = DownloadsManager(str(tmp_path), lambda: {})
    monkeypatch.setattr(manager, "_start_download", lambda _record: None)
    previous = DownloadRecord(id="old-theme", display_name="Theme", target_kind=TargetKind.THEME,
                              canonical_key="theme:test:1", download_status=DownloadStatus.DOWNLOADED,
                              use_status=UseStatus.READY, ever_installed=True, file_exists=False)
    manager.store.add(previous)
    record_id, duplicate = manager.enqueue("Theme", target_kind=TargetKind.THEME, canonical_key="theme:test:1")
    assert not duplicate
    assert record_id != previous.id
    assert manager.store.find(previous.id) is None


def test_late_worker_success_cannot_replace_cancelled_state(tmp_path) -> None:
    manager = DownloadsManager(str(tmp_path), lambda: {})
    record = DownloadRecord(
        id="download-race",
        display_name="Race",
        download_status=DownloadStatus.DOWNLOADING,
    )
    manager.store.add(record)

    manager.action_cancel_download(record.id)
    manager._on_download_finished(record.id, True, "", str(tmp_path / "late.zip"))

    assert record.download_status == DownloadStatus.CANCELLED
    assert record.use_status == UseStatus.CANCELLED
    assert record.file_exists is False


def test_stale_retry_generation_cannot_remove_active_worker(tmp_path) -> None:
    manager = DownloadsManager(str(tmp_path), lambda: {})
    record = DownloadRecord(
        id="download-retry-race",
        display_name="Race",
        download_status=DownloadStatus.DOWNLOADING,
    )
    manager.store.add(record)
    active_worker = object()
    manager._workers[record.id] = active_worker
    manager._download_generations[record.id] = 2

    manager._on_download_finished(
        record.id, True, "", str(tmp_path / "stale.zip"), generation=1
    )

    assert manager._workers[record.id] is active_worker
    assert record.download_status == DownloadStatus.DOWNLOADING


def test_clear_downloads_removes_generation_for_deleted_record(tmp_path) -> None:
    manager = DownloadsManager(str(tmp_path), lambda: {})
    record = DownloadRecord(
        id="done", display_name="Done", download_status=DownloadStatus.DOWNLOADED
    )
    manager.store.add(record)
    manager._download_generations[record.id] = 3

    manager.clear_downloads()

    assert record.id not in manager._download_generations


def test_manual_setup_result_is_not_reported_as_installed(tmp_path) -> None:
    manager = DownloadsManager(str(tmp_path), lambda: {})
    record = DownloadRecord(
        id="wip-84933",
        display_name="DELTARUNE Multiplayer Mod!",
        download_status=DownloadStatus.DOWNLOADED,
        use_status=UseStatus.USING,
        file_path=str(tmp_path / "multiplayer.zip"),
        file_exists=True,
    )
    manager.store.add(record)
    completions = []
    manager.use_completed.connect(lambda: completions.append(True))

    manager._on_use_finished(record.id, False, True, "")

    assert record.use_status == UseStatus.NEEDS_MANUAL
    assert record.ever_installed is False
    assert completions == []


def test_gamebanana_update_uses_its_snapshotted_mods_directory(tmp_path, monkeypatch) -> None:
    queued_profile = tmp_path / "queued-profile"
    active_profile = tmp_path / "active-profile"
    queued_profile.mkdir()
    active_profile.mkdir()
    archive = tmp_path / "update.zip"
    archive.write_bytes(b"archive")
    manager = DownloadsManager(str(tmp_path), lambda: {})
    manager.set_app_context(mods_dir=str(active_profile))
    record = DownloadRecord(
        id="profile-bound-update",
        display_name="Update",
        target_kind=TargetKind.MOD,
        file_path=str(archive),
        file_exists=True,
        metadata={
            "target_mods_dir": str(queued_profile),
            "gb_mod_id": 42,
            "gb_file_id": 77,
            "timestamp": 123,
        },
    )
    manager.store.add(record)
    captured = {}

    class Worker:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.use_finished = SimpleNamespace(connect=lambda _callback: None)

        def start(self) -> None:
            pass

    monkeypatch.setattr("workers.use_worker.UseWorker", Worker)

    manager._start_use(record.id)
    manager._remember_gamebanana_file(record)

    assert captured["mods_dir"] == str(queued_profile)
    assert json.loads((queued_profile / "mods_data.json").read_text(encoding="utf-8")) == {
        "gb_mod_42": {
            "gamebanana_file_id": 77,
            "gamebanana_file_timestamp": 123,
        }
    }
    assert not (active_profile / "mods_data.json").exists()
