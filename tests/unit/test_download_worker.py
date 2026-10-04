"""Unit tests for download worker crash handling."""

from __future__ import annotations

from unittest.mock import Mock

from helpers import FailingSignal

from workers.download_worker import DownloadWorker, _verify_md5


def test_cancellation_during_checksum_never_reports_download_success(qapp, tmp_path, monkeypatch):
    target = tmp_path / "mod.zip"
    worker = DownloadWorker("record", "https://gamebanana.com/dl/1", str(target), expected_md5="0" * 32)
    monkeypatch.setattr("utils.network_utils.get_session", object)
    monkeypatch.setattr("utils.network_utils.download_file", lambda *_args, **_kwargs: target.write_bytes(b"payload"))

    def verify(path, expected, **kwargs):
        worker.cancel()
        return _verify_md5(path, expected, **kwargs)

    monkeypatch.setattr("workers.download_worker._verify_md5", verify)
    finished = []
    worker.download_finished.connect(lambda *args: finished.append(args))

    worker.run()

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


def test_download_worker_rejects_checksum_mismatch(qapp, tmp_path, monkeypatch):
    target = tmp_path / "mod.zip"
    worker = DownloadWorker(
        "record_1",
        "https://gamebanana.com/dl/1",
        str(target),
        expected_md5="0" * 32,
    )
    finished = []
    worker.download_finished.connect(lambda *args: finished.append(args))
    monkeypatch.setattr("utils.network_utils.get_session", object)

    def write_download(_session, _url, path, **_kwargs):
        target.write_bytes(b"payload")

    monkeypatch.setattr("utils.network_utils.download_file", write_download)

    worker.run()

    assert finished and finished[0][1] is False
    assert not target.exists()
