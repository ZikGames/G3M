"""Background worker for cancellable plugin hook execution."""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal

from models.plugin_models import PluginTaskRuntime
from services.background_operations import background_operations
from services.localization_service import tr
from ui.utils.thread_lifetime import ManagedQThread
from ui.utils.thread_lifetime import safe_emit as _safe_emit

logger = logging.getLogger(__name__)


class PluginHookThread(ManagedQThread):
    """Runs a plugin hook with shared progress and cancellation support."""

    progress_update = pyqtSignal(int, str)
    status_update = pyqtSignal(str, str)
    result_ready = pyqtSignal(bool)

    def __init__(
        self,
        runtime_service,
        hook_name: str,
        hook_args: tuple,
        *,
        base_progress: int,
        progress_span: int,
        target_plugin_id: str | None = None,
        cancel_hook: str = "mod_apply_cancelled",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runtime_service = runtime_service
        self.hook_name = hook_name
        self.hook_args = hook_args
        self.base_progress = int(base_progress)
        self.progress_span = max(0, int(progress_span))
        self.target_plugin_id = target_plugin_id
        self.cancel_hook = cancel_hook
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()
        background_operations.cancel_processes(owner=self)
        _safe_emit(
            self.__class__.__name__,
            self.status_update,
            tr("status.operation_cancelled"),
            "error",
        )

    def _is_cancelled(self) -> bool:
        return self._cancelled or self.isInterruptionRequested()

    def _emit_progress(self, progress: int, message: str = "") -> None:
        bounded = max(0, min(int(progress), 100))
        mapped = self.base_progress + round((self.progress_span * bounded) / 100)
        _safe_emit(
            self.__class__.__name__,
            self.progress_update,
            max(0, min(mapped, 100)),
            message,
        )

    def _emit_status(self, message: str, status_type: str = "info") -> None:
        _safe_emit(self.__class__.__name__, self.status_update, message, status_type)

    def _build_task_runtime(self) -> PluginTaskRuntime:
        return PluginTaskRuntime(
            set_progress_callback=self._emit_progress,
            set_status_callback=self._emit_status,
            is_cancelled_callback=self._is_cancelled,
            track_process_callback=lambda process, cancel=None: background_operations.track_process(
                process, cancel=cancel, owner=self
            ),
        )

    def _execute_hook(self, hook_name: str, task_runtime, *args):
        kwargs = (
            {"target_plugin_id": self.target_plugin_id}
            if self.target_plugin_id is not None
            else {}
        )
        return self.runtime_service.execute_hook_with_runtime(
            hook_name, task_runtime, *args, **kwargs
        )

    def _run_cancel_hook(self, task_runtime, reason: str) -> None:
        try:
            if task_runtime is None:
                task_runtime = self._build_task_runtime()
            self._execute_hook(
                self.cancel_hook,
                task_runtime,
                {"hook": self.hook_name, "reason": reason},
                *self.hook_args,
            )
        except Exception:
            logger.exception("PluginHookThread cancellation hook failed")

    def run(self) -> None:
        success = False
        task_runtime = None
        try:
            task_runtime = self._build_task_runtime()
            self._emit_progress(0, "")
            results = self._execute_hook(
                self.hook_name,
                task_runtime,
                *self.hook_args,
            )
            if self._is_cancelled():
                self._run_cancel_hook(task_runtime, "cancelled")
                success = False
            else:
                success = not any(result is False for result in results)
                if not success:
                    self._run_cancel_hook(task_runtime, "failed")
            self._emit_progress(100 if success else 0, "")
        except InterruptedError:
            self._run_cancel_hook(task_runtime, "cancelled")
            success = False
        except Exception as error:
            logger.error("PluginHookThread failed: %s", error, exc_info=True)
            _safe_emit(
                self.__class__.__name__,
                self.status_update,
                tr("errors.plugin_hook_failed"),
                "error",
            )
            self._run_cancel_hook(task_runtime, "failed")
            success = False
        finally:
            _safe_emit(self.__class__.__name__, self.result_ready, success)
