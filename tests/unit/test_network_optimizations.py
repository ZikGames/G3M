"""Unit tests for network-sensitive refresh behavior."""

import threading
from collections.abc import Callable
from unittest.mock import Mock, patch

import pytest
from PyQt6.QtCore import QObject

from presentation.update_presenter import reload_global_settings


def test_download_file_uses_get_length_when_head_omits_it(tmp_path):
    from utils.network_utils import download_file

    response = Mock()
    response.status_code = 200
    response.headers = {"content-length": "6"}
    response.iter_content.return_value = [b"abc", b"def"]
    session = Mock()
    session.head.return_value = Mock(headers={})
    session.get.return_value = response
    progress = []
    received = [0]

    download_file(
        session,
        "https://example.com/mod.zip",
        str(tmp_path / "mod.zip"),
        progress_callback=progress.append,
        downloaded_ref=received,
    )

    assert progress == [50, 100]
    assert received == [6]


def test_trusted_download_rejects_redirect_before_following_it(tmp_path):
    from utils.network_utils import download_file

    session = Mock()
    session.head.return_value = Mock(
        status_code=302,
        headers={"location": "https://example.com/mod.zip"},
    )

    with pytest.raises(RuntimeError, match="trusted HTTPS hosts"):
        download_file(
            session,
            "https://gamebanana.com/dl/1",
            str(tmp_path / "mod.zip"),
            allowed_hosts=frozenset({"gamebanana.com"}),
        )

    session.get.assert_not_called()


def test_get_session_isolated_between_threads(monkeypatch):
    from utils import network_utils

    sessions = [object(), object()]
    monkeypatch.setattr(network_utils, "_thread_local", threading.local())
    monkeypatch.setattr(network_utils, "_build_session", Mock(side_effect=sessions))
    main_session = network_utils.get_session()
    worker_sessions = []
    worker = threading.Thread(target=lambda: worker_sessions.append(network_utils.get_session()))

    worker.start()
    worker.join()

    assert worker_sessions == [sessions[1]]
    assert worker_sessions[0] is not main_session


def test_reload_global_settings_skips_refresh_when_cached():
    """Checks that reloading global settings skips refresh when cached."""
    app = Mock()
    app.app_state = Mock()
    app.app_state.has_internet = True
    app.app_state.global_settings = {"announce": {}}
    app.app_state.global_settings_loaded_at = 900
    app.app_state.global_settings_load_in_progress = False
    app.app_state.initialization_completed = True
    app.app_state.is_shown_to_user = True
    app.isVisible.return_value = True
    callback = Mock()

    with patch("presentation.update_presenter.time.time", return_value=1000.0):
        reload_global_settings(app, callback=callback)

    callback.assert_called_once_with(True)
    assert app.app_state.global_settings_load_in_progress is False


def test_reload_global_settings_force_refresh_ignores_cache():
    """Checks that reloading global settings force refresh ignores cache."""
    app = Mock()
    app.app_state = Mock()
    app.app_state.has_internet = True
    app.app_state.global_settings = {"announce": {}}
    app.app_state.global_settings_loaded_at = 1000.0
    app.app_state.global_settings_load_in_progress = False
    callback = Mock()

    response = Mock()
    response.status_code = 200
    response.json.return_value = {"announce": {"version": 2}}
    session = Mock()
    session.get.return_value = response

    with patch("presentation.update_presenter.get_session", return_value=session), patch(
        "presentation.update_presenter.time.time", side_effect=[2000.0, 2000.0]
    ):
        reload_global_settings(app, callback=callback, force_refresh=True)

    callback.assert_called_once_with(True)
    assert app.app_state.global_settings["announce"]["version"] == 2
    assert session.get.called


def test_reload_global_settings_suppresses_callback_failure_when_cached():
    """Checks that a refresh callback error is logged instead of crashing the caller."""
    app = Mock()
    app.app_state = Mock()
    app.app_state.has_internet = True
    app.app_state.global_settings = {"announce": {}}
    app.app_state.global_settings_loaded_at = 900
    app.app_state.global_settings_load_in_progress = False
    callback = Mock(side_effect=RuntimeError("callback failed"))

    with patch("presentation.update_presenter.time.time", return_value=1000.0):
        reload_global_settings(app, callback=callback)

    callback.assert_called_once_with(True)
    assert app.app_state.global_settings_load_in_progress is False


def test_reload_global_settings_suppresses_callback_failure_from_worker(qapp, monkeypatch):
    """Checks that the Qt worker completion callback cannot crash global settings refresh."""
    from presentation import update_presenter

    app = QObject()
    app.app_state = Mock()
    app.app_state.has_internet = True
    app.app_state.global_settings = {}
    app.app_state.global_settings_load_in_progress = False
    callback = Mock(side_effect=RuntimeError("callback failed"))

    class _Signal:
        def __init__(self) -> None:
            self._callback: Callable[..., object] | None = None

        def connect(self, callback):
            self._callback = callback

    class _Worker:
        def __init__(self, *_args, **_kwargs) -> None:
            self.result_ready = _Signal()

        def start(self):
            callback = self.result_ready._callback
            assert callback is not None
            callback(True, {"announce": {"version": 2}})

        def deleteLater(self):  # noqa: N802
            return None

    monkeypatch.setattr(update_presenter, "_GlobalSettingsWorker", _Worker)

    reload_global_settings(app, callback=callback, force_refresh=True)

    callback.assert_called_once_with(True)
    assert app.app_state.global_settings == {"announce": {"version": 2}}
    assert app.app_state.global_settings_load_in_progress is False
