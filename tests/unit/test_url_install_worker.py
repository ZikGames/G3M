"""Unit tests for URL install worker failure paths."""

import pytest
import requests

from workers.install.url_install_worker import InstallDeclinedError, UrlInstallThread


class _UrlThatFailsBeforeNormalization:
    def startswith(self, _prefixes):
        raise requests.RequestException("protocol parse failed")


def test_url_install_worker_reports_malformed_url_without_handler_crash(qapp):
    """Checks that malformed URL input emits failure instead of crashing in except."""
    worker = UrlInstallThread(None, _UrlThatFailsBeforeNormalization())
    finished = []
    worker.result_ready.connect(lambda success, message: finished.append((success, message)))

    worker.run()

    assert finished
    assert finished[-1][0] is False
    assert finished[-1][1]


def test_url_install_worker_suppresses_emit_failure_after_error(qapp, caplog):
    """Checks that URL install errors cannot crash while notifying a dead UI."""
    worker = UrlInstallThread(None, _UrlThatFailsBeforeNormalization())

    class _FailingSignal:
        def emit(self, *_args, **_kwargs):
            raise RuntimeError("receiver deleted")

    vars(worker)["result_ready"] = _FailingSignal()

    worker.run()

    assert "UrlInstallThread: failed to emit" in caplog.text


def test_declining_the_versions_of_a_redirected_mod_is_not_swallowed(qapp, tmp_path, monkeypatch):
    """Cancelling the version dialog of a redirect target must stop the install."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "mod_config.json").write_text('{"download_url": "https://example.com/real.zip"}', encoding="utf-8")
    worker = UrlInstallThread(None, "https://example.com/stub.zip")

    def decline(_url):
        raise InstallDeclinedError

    monkeypatch.setattr(worker, "_unpack_content_path", lambda *_args: str(stub))
    monkeypatch.setattr(worker, "_process_deltamod_archive", decline)

    with pytest.raises(InstallDeclinedError):
        worker._check_redirect("stub.zip", str(tmp_path))


def test_manual_install_chosen_for_a_redirected_mod_opens_manual_installation(qapp, tmp_path, monkeypatch):
    from workers.install.url_install_worker import ManualInstallChosenError

    content = tmp_path / "content"
    content.mkdir()
    (content / "mod_config.json").write_text("{}", encoding="utf-8")
    worker = UrlInstallThread(None, "https://example.com/stub.zip")
    prepared = []

    def choose_manual(_path):
        raise ManualInstallChosenError

    monkeypatch.setattr(worker, "_download_archive", lambda *_args: str(tmp_path / "real.zip"))
    monkeypatch.setattr(worker, "_unpack_content_path", lambda *_args: str(content))
    monkeypatch.setattr(worker, "_install_g3m_mod_from_path", choose_manual)
    monkeypatch.setattr(worker, "_prepare_for_manual_install", prepared.append)

    worker._process_deltamod_archive("https://example.com/real.zip")

    assert prepared == [str(tmp_path / "real.zip")]
