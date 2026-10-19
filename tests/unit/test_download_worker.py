"""Unit tests for download worker crash handling."""

from __future__ import annotations

import hashlib
import os
from unittest.mock import Mock

from helpers import FailingSignal

from workers.download_worker import CHECKSUM_WARNING, DownloadWorker, _file_md5


def test_cancellation_during_checksum_never_reports_download_success(qapp, tmp_path, monkeypatch):
    target = tmp_path / "mod.zip"
    worker = DownloadWorker("record", "https://gamebanana.com/dl/1", str(target), expected_md5="0" * 32)
    real_md5 = _file_md5

    def file_md5(path, **kwargs):
        worker.cancel()
        return real_md5(path, **kwargs)

    monkeypatch.setattr("workers.download_worker._file_md5", file_md5)

    finished = _run_download(worker, target, monkeypatch)

    assert len(finished) == 1
    assert finished[0][1] is False
    assert not target.exists()


def test_download_worker_suppresses_emit_failure_after_download_error(
    qapp, tmp_path, monkeypatch, caplog
):
    """Checks that handled download errors cannot crash while notifying a dead UI."""
    worker = DownloadWorker(
        "record_1",
        "https://example.invalid/mod.zip",
        str(tmp_path / "mod.zip"),
    )
    vars(worker)["download_finished"] = FailingSignal()

    class _Session:
        def head(self, *_args, **_kwargs):
            return type("_Response", (), {"headers": {}})()

    monkeypatch.setattr("utils.network_utils.get_session", lambda: _Session())
    monkeypatch.setattr("utils.network_utils.download_file", Mock(side_effect=RuntimeError("download failed")))

    worker.run()

    assert "DownloadWorker: download failed" in caplog.text
    assert "DownloadWorker: failed to emit" in caplog.text


def _run_download(worker, target, monkeypatch, payload=b"payload"):
    finished = []
    worker.download_finished.connect(lambda *args: finished.append(args))
    monkeypatch.setattr("utils.network_utils.get_session", object)
    monkeypatch.setattr(
        "utils.network_utils.download_file",
        lambda _session, _url, path, **_kwargs: target.write_bytes(payload),
    )
    worker.run()
    return finished


def test_download_worker_warns_about_checksum_mismatch_and_keeps_file(qapp, tmp_path, monkeypatch):
    target = tmp_path / "mod.zip"
    worker = DownloadWorker("record_1", "https://gamebanana.com/dl/1", str(target), expected_md5="0" * 32)

    finished = _run_download(worker, target, monkeypatch)

    assert finished[0][1] is True
    assert finished[0][2] == CHECKSUM_WARNING
    assert target.exists()


def test_download_worker_accepts_file_matching_refreshed_checksum(qapp, tmp_path, monkeypatch):
    target = tmp_path / "mod.zip"
    current = hashlib.md5(b"payload", usedforsecurity=False).hexdigest()
    worker = DownloadWorker(
        "record_1",
        "https://gamebanana.com/dl/1",
        str(target),
        expected_md5="0" * 32,
        md5_refresh=lambda: current,
    )

    finished = _run_download(worker, target, monkeypatch)

    assert finished[0][1:3] == (True, "")


def test_download_worker_discards_stale_partial_file_before_downloading(qapp, tmp_path, monkeypatch):
    target = tmp_path / "mod.zip"
    target.write_bytes(b"old partial")
    worker = DownloadWorker("record_1", "https://gamebanana.com/dl/1", str(target))
    seen = []
    monkeypatch.setattr("utils.network_utils.get_session", object)
    monkeypatch.setattr(
        "utils.network_utils.download_file",
        lambda _session, _url, path, **_kwargs: seen.append(os.path.exists(path)),
    )

    worker.run()

    assert seen == [False]
