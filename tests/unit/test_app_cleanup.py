"""Unit tests for test app cleanup."""

from unittest.mock import Mock, patch

from PyQt6.QtCore import QObject, QThread


def test_iter_shutdown_threads_collects_direct_and_container_threads(qapp):
    from app.cleanup import _iter_shutdown_threads

    root = QObject()
    direct = QThread(root)
    child = QObject(root)
    vars(child)["worker_thread"] = QThread(child)
    vars(child)["_workers"] = {"a": QThread(child)}

    thread_entries = list(_iter_shutdown_threads(root))
    threads = [entry[0] for entry in thread_entries]

    assert direct in threads
    assert vars(child)["worker_thread"] in threads
    assert vars(child)["_workers"]["a"] in threads
    assert len({id(thread) for thread in threads}) == len(threads)


def test_perform_close_cleanup_stops_discovered_threads(qapp):
    from app.cleanup import perform_close_cleanup

    root = QObject()
    vars(root)["_safe_set_parent_none"] = Mock()
    vars(root)["customization_service"] = Mock()
    vars(root)["plugin_runtime_service"] = Mock()
    vars(root)["session_manager"] = Mock()
    vars(root)["search_display"] = Mock()
    vars(root)["game_launcher"] = Mock()
    vars(root)["game_launcher"].monitor_thread = None
    vars(root)["refresh_controller"] = Mock()
    vars(root)["refresh_controller"].fetch_thread = None
    vars(root)["refresh_controller"].details_thread = None
    vars(root)["settings_service"] = Mock()
    vars(root)["main_tab_widget"] = Mock()
    vars(root)["main_tab_widget"].currentIndex.return_value = 0
    vars(root)["app_state"] = Mock()
    vars(root)["app_state"].local_config = {}
    vars(root)["hide"] = Mock()
    child = QObject(root)
    vars(child)["thread"] = QThread(child)
    vars(child)["_workers"] = {"download": QThread(child)}

    with (
        patch("app.cleanup.safe_stop_thread") as safe_stop_thread,
        patch("app.cleanup.QThreadPool.globalInstance") as pool_instance,
        patch("app.cleanup.shutdown_compatibility_job_pool") as shutdown_pool,
        patch("app.cleanup.QApplication.processEvents"),
    ):
        pool = Mock()
        pool_instance.return_value = pool
        perform_close_cleanup(root)

    stopped = [call.args[0] for call in safe_stop_thread.call_args_list]
    assert child.thread in stopped
    assert vars(child)["_workers"]["download"] in stopped
    pool.clear.assert_called_once_with()
    pool.waitForDone.assert_called_once()
    shutdown_pool.assert_called_once()
    vars(root)["game_launcher"]._cleanup_direct_launch_files.assert_called_once_with(blocking=True)


def test_perform_close_cleanup_stops_threads_on_non_qobject_controllers(qapp):
    from app.cleanup import perform_close_cleanup

    root = QObject()
    vars(root)["_safe_set_parent_none"] = Mock()
    vars(root)["customization_service"] = Mock()
    vars(root)["plugin_runtime_service"] = Mock()
    vars(root)["session_manager"] = Mock()
    vars(root)["search_display"] = Mock()
    vars(root)["game_launcher"] = Mock()
    vars(root)["game_launcher"].monitor_thread = None
    vars(root)["refresh_controller"] = Mock()
    vars(root)["refresh_controller"].fetch_thread = QThread()
    vars(root)["refresh_controller"].details_thread = QThread()
    vars(root)["settings_service"] = Mock()
    vars(root)["main_tab_widget"] = Mock()
    vars(root)["main_tab_widget"].currentIndex.return_value = 0
    vars(root)["app_state"] = Mock()
    vars(root)["app_state"].local_config = {}
    vars(root)["hide"] = Mock()

    with (
        patch("app.cleanup.safe_stop_thread") as safe_stop_thread,
        patch("app.cleanup.QThreadPool.globalInstance") as pool_instance,
        patch("app.cleanup.QApplication.processEvents"),
    ):
        pool_instance.return_value = Mock()
        perform_close_cleanup(root)

    stopped = [call.args[0] for call in safe_stop_thread.call_args_list]
    assert vars(root)["refresh_controller"].fetch_thread in stopped
    assert vars(root)["refresh_controller"].details_thread in stopped


def test_perform_close_cleanup_skips_threads_managed_by_session(qapp):
    from app.cleanup import perform_close_cleanup

    root = QObject()
    vars(root)["_safe_set_parent_none"] = Mock()
    vars(root)["customization_service"] = Mock()
    vars(root)["plugin_runtime_service"] = Mock()
    vars(root)["session_manager"] = QObject(root)
    session_thread = QThread(vars(root)["session_manager"])
    vars(root)["session_manager"].thread = session_thread
    vars(root)["search_display"] = Mock()
    vars(root)["game_launcher"] = Mock()
    vars(root)["game_launcher"].monitor_thread = None
    vars(root)["refresh_controller"] = Mock()
    vars(root)["refresh_controller"].fetch_thread = None
    vars(root)["refresh_controller"].details_thread = None
    vars(root)["settings_service"] = Mock()
    vars(root)["main_tab_widget"] = Mock()
    vars(root)["main_tab_widget"].currentIndex.return_value = 0
    vars(root)["app_state"] = Mock()
    vars(root)["app_state"].local_config = {}
    vars(root)["hide"] = Mock()

    with (
        patch("app.cleanup.safe_stop_thread") as safe_stop_thread,
        patch("app.cleanup.QThreadPool.globalInstance") as pool_instance,
        patch("app.cleanup.QApplication.processEvents"),
    ):
        pool = Mock()
        pool_instance.return_value = pool
        perform_close_cleanup(root)

    stopped = [call.args[0] for call in safe_stop_thread.call_args_list]
    assert session_thread not in stopped
