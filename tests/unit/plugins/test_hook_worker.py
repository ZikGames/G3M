"""Unit tests for test hook worker."""

from unittest.mock import Mock

from models.plugin_models import PluginLaunchAction
from services.launch_service import GameLauncher
from workers.plugin_hook_worker import PluginHookThread


def test_plugin_hook_worker_maps_progress_and_executes_hook():
    runtime_service = Mock()

    def _run_hook(hook_name, task_runtime, *_args):
        assert hook_name == "after_mod_apply_before_launch"
        task_runtime.set_progress(50, "half")
        return [True]

    runtime_service.execute_hook_with_runtime.side_effect = _run_hook
    thread = PluginHookThread(
        runtime_service,
        "after_mod_apply_before_launch",
        ({"deltarune_1": []}, False),
        base_progress=96,
        progress_span=4,
    )
    progress = []
    status = []
    finished = []
    thread.progress_update.connect(lambda value, message: progress.append((value, message)))
    thread.status_update.connect(lambda message, level: status.append((message, level)))
    thread.result_ready.connect(lambda ok: finished.append(ok))

    thread.run()

    assert (98, "half") in progress
    assert finished == [True]
    assert status == []


def test_plugin_hook_worker_calls_cancel_hook_when_cancelled():
    runtime_service = Mock()

    def _run_hook(hook_name, task_runtime, *_args):
        if hook_name == "mod_apply_cancelled":
            return [True]
        thread.cancel()
        task_runtime.raise_if_cancelled()
        return [True]

    runtime_service.execute_hook_with_runtime.side_effect = _run_hook
    thread = PluginHookThread(
        runtime_service,
        "after_mod_apply_before_launch",
        ({}, False),
        base_progress=0,
        progress_span=100,
    )
    finished = []
    thread.result_ready.connect(lambda ok: finished.append(ok))

    thread.run()

    assert finished == [False]
    assert runtime_service.execute_hook_with_runtime.call_args_list[-1].args[0] == "mod_apply_cancelled"


def test_plugin_hook_worker_logs_failed_emit_after_hook_error(caplog):
    runtime_service = Mock()
    runtime_service.execute_hook_with_runtime.side_effect = RuntimeError("hook failed")
    thread = PluginHookThread(
        runtime_service,
        "after_mod_apply_before_launch",
        ({}, False),
        base_progress=0,
        progress_span=100,
    )

    class _FailingSignal:
        def emit(self, *_args, **_kwargs):
            raise RuntimeError("receiver deleted")

    thread.status_update = _FailingSignal()
    thread.result_ready = _FailingSignal()

    thread.run()

    assert "PluginHookThread failed" in caplog.text
    assert "PluginHookThread: failed to emit" in caplog.text


def test_plugin_hook_worker_runs_cleanup_after_unexpected_error():
    runtime_service = Mock()

    def _run_hook(hook_name, _task_runtime, *_args):
        if hook_name == "mod_apply_cancelled":
            return [True]
        raise RuntimeError("hook failed")

    runtime_service.execute_hook_with_runtime.side_effect = _run_hook
    thread = PluginHookThread(
        runtime_service,
        "after_mod_apply_before_launch",
        ({}, False),
        base_progress=0,
        progress_span=100,
    )

    thread.run()

    assert runtime_service.execute_hook_with_runtime.call_args_list[-1].args[0] == (
        "mod_apply_cancelled"
    )


def test_plugin_launch_action_receives_its_local_id():
    launcher = GameLauncher(Mock(), Mock(), Mock())
    launcher._start_plugin_hook_thread = Mock(return_value=True)
    action = PluginLaunchAction(
        id="plugin:news_plugin:refresh",
        label="Refresh",
        plugin_id="news_plugin",
    )

    assert launcher.run_plugin_launch_action(action) is True
    assert launcher._start_plugin_hook_thread.call_args.args[:2] == (
        "launch_action",
        "refresh",
    )
