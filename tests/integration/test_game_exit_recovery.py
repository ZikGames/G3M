"""Exercise process exit and restoration through the Qt event loop."""

import subprocess
import sys
import time
from unittest.mock import Mock

from PyQt6.QtCore import QObject

from services.launch_service import GameLauncher
from services.launch_transaction import LaunchState
from services.mod.service import ModManager
from services.mod_operation_executor import ModOperationJournal
from utils.file_utils import load_json, save_json
from workers.game_monitor_worker import GameMonitorWorker


def test_repeated_process_exit_restores_files(
    qtbot, app_state, feedback_service, tmp_path, monkeypatch
):
    monkeypatch.setattr(GameMonitorWorker, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(GameMonitorWorker, "_RUNNING_POLL_INTERVAL_SECONDS", 0.01)
    parent = QObject()
    mod_service = ModManager(app_state, feedback_service)
    parent.mod_service = mod_service
    launcher = GameLauncher(app_state, feedback_service, mod_service, parent)
    target = tmp_path / "data.win"
    target.write_bytes(b"ORIGINAL")
    completed = []
    launcher.game_launch_finished.connect(lambda: completed.append(True))

    for run in range(3):
        save_json(app_state.mods_metadata_path, {"mod": None})
        launcher._launch_mod_ids = ["mod"]
        launcher._launch_started_at = time.monotonic()
        journal = ModOperationJournal(tmp_path / f"operation-{run}")
        journal.capture(target)
        target.write_bytes(f"MOD-{run}".encode())
        journal.seal()
        launcher._operation_journal = journal
        launcher.launch_transaction.begin()
        launcher.launch_transaction.mark_launching()
        launcher.launch_transaction.mark_running()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            launcher._game_process = process
            launcher._start_game_monitor(process, False, (), set())
            qtbot.waitUntil(lambda: launcher.monitor_thread.isRunning())
            qtbot.wait(100)
            process.terminate()
            process.wait(timeout=5)
            qtbot.waitUntil(
                lambda expected=run + 1: len(completed) == expected, timeout=5000
            )
            assert target.read_bytes() == b"ORIGINAL"
            assert launcher.launch_transaction.state == LaunchState.COMPLETED
            assert launcher.monitor_thread is None
            assert app_state.is_patching is False
            assert load_json(app_state.mods_metadata_path) == {"mod": None}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            launcher._stop_monitor_thread()


def test_game_exit_keeps_external_changes_after_confirmation(
    qtbot, app_state, feedback_service, tmp_path
):
    launcher = GameLauncher(app_state, feedback_service, Mock())
    target = tmp_path / "data.win"
    target.write_bytes(b"ORIGINAL")
    journal = ModOperationJournal(tmp_path / "operation")
    journal.capture(target)
    target.write_bytes(b"MOD")
    journal.seal()
    target.write_bytes(b"GAME SAVE")
    launcher._operation_journal = journal
    launcher.launch_transaction.begin()
    launcher.launch_transaction.mark_launching()
    launcher.launch_transaction.mark_running()
    feedback_service.ask_operation_recovery_conflict = Mock(return_value="keep")
    completed = []
    launcher.game_launch_finished.connect(lambda: completed.append(True))

    launcher._check_game_running(False)

    qtbot.waitUntil(lambda: bool(completed))
    assert target.read_bytes() == b"GAME SAVE"
    assert journal.state == "retired"
    feedback_service.ask_operation_recovery_conflict.assert_called_once()


def test_game_exit_accepts_tracked_plugin_restoration(
    qtbot, app_state, feedback_service, tmp_path
):
    parent = QObject()
    parent.plugin_runtime_service = Mock()
    launcher = GameLauncher(app_state, feedback_service, Mock(), parent)
    target = tmp_path / "data.win"
    target.write_bytes(b"ORIGINAL")
    journal = ModOperationJournal(tmp_path / "operation")
    journal.capture(target)
    target.write_bytes(b"MOD")
    journal.seal()
    target.write_bytes(b"PLUGIN")
    journal.checkpoint()
    launcher._operation_journal = journal
    launcher.launch_transaction.begin()
    launcher.launch_transaction.mark_launching()
    launcher.launch_transaction.mark_running()
    feedback_service.ask_operation_recovery_conflict = Mock()
    completed = []
    launcher.game_launch_finished.connect(lambda: completed.append(True))

    def run_hook(name, _task_runtime, *_args, **_kwargs):
        if name == "before_restore_after_exit":
            target.write_bytes(b"MOD")
        return []

    parent.plugin_runtime_service.execute_hook_with_runtime.side_effect = run_hook
    launcher._check_game_running(False)

    qtbot.waitUntil(lambda: bool(completed))
    assert target.read_bytes() == b"ORIGINAL"
    assert journal.state == "restored"
    feedback_service.ask_operation_recovery_conflict.assert_not_called()
