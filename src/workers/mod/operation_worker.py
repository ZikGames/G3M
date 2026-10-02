"""Background execution for one validated operation mod operation plan."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from services.localization_service import tr
from services.mod_operation_executor import (
    ModOperationCancelledError,
    ModOperationExecutor,
    ModOperationJournal,
)
from services.mod_operation_support import create_g3mtool_merger, create_g3mtool_patcher
from ui.utils.thread_lifetime import ManagedQThread
from ui.utils.thread_lifetime import safe_emit as _safe_emit
from utils.mod.archive import ArchiveVirtualPath
from utils.mod.operation_plan import ModOperationPlan, PlannedModOperation

logger = logging.getLogger(__name__)


def _display_path(value: Path | ArchiveVirtualPath | None) -> str:
    if value is None:
        return ""
    if isinstance(value, ArchiveVirtualPath):
        member = value.member.rstrip("/")
        return member.rsplit("/", 1)[-1] if member else value.archive.name
    return value.name or str(value)


def _log_path(value: Path | ArchiveVirtualPath | None) -> str:
    if value is None:
        return ""
    if isinstance(value, ArchiveVirtualPath):
        return f"{value.archive}!/{value.member}"
    return str(value)


def _operation_status_key(operation_type: str) -> str:
    return {
        "patch": "operation_patching",
        "overwrite": "operation_overwriting",
        "soft-overwrite": "operation_soft_overwriting",
        "hard-overwrite": "operation_hard_overwriting",
        "extract": "operation_extracting",
        "soft-extract": "operation_soft_extracting",
        "hard-extract": "operation_hard_extracting",
        "info": "operation_inspecting",
    }.get(operation_type, "operation_processing")


def _patch_format(value: Path | ArchiveVirtualPath) -> str:
    suffix = Path(_display_path(value)).suffix.casefold()
    return {
        ".xdelta": "XDELTA",
        ".vcdiff": "VCDIFF",
        ".g3mpatch": "G3MPatch",
        ".csx": "CSX",
    }.get(suffix, tr("status.patch_format_generic"))


class ModOperationThread(ManagedQThread):
    """Apply ordered mod operations off the UI thread and retain their journal."""

    progress_update = pyqtSignal(int, str)
    status_update = pyqtSignal(str, str)
    result_ready = pyqtSignal(bool)

    def __init__(
        self,
        app_state,
        plan: ModOperationPlan,
        journal_root: str | Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.plan = plan
        self.journal_root = Path(journal_root)
        self.journal: ModOperationJournal | None = None
        self._cancelled = False
        self._patcher = create_g3mtool_patcher(app_state, is_cancelled=self._is_cancelled)
        self._merger = create_g3mtool_merger(app_state, is_cancelled=self._is_cancelled)

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def _is_cancelled(self) -> bool:
        return self._cancelled or self.isInterruptionRequested()

    def _on_progress(
        self, completed: int, total: int, operation: PlannedModOperation
    ) -> None:
        progress = int(completed * 100 / max(total, 1))
        target = operation.target or operation.source
        merged = ModOperationExecutor.merge_operations(self.plan.operations, operation)
        if merged:
            patch_types = ", ".join(
                dict.fromkeys(_patch_format(item.source) for item in merged)
            )
            logger.info(
                "Merged mod patches: indexes=%s count=%s patch_types=%s progress=%s/%s target=%s",
                [item.index for item in merged],
                len(merged),
                patch_types,
                completed,
                total,
                _log_path(operation.target),
            )
            message = tr(
                "status.operation_merging",
                count=len(merged),
                patch_types=patch_types,
                target=_display_path(target),
                current=completed,
                total=total,
            )
        else:
            patch_type = _patch_format(operation.source) if operation.type == "patch" else ""
            logger.info(
                "Mod operation completed: index=%s type=%s patch_type=%s progress=%s/%s source=%s target=%s",
                operation.index,
                operation.type,
                patch_type,
                completed,
                total,
                _log_path(operation.source),
                _log_path(operation.target),
            )
            message = tr(
                f"status.{_operation_status_key(operation.type)}",
                patch_type=patch_type,
                from_file=_display_path(operation.source),
                target=_display_path(target),
                current=completed,
                total=total,
            )
        _safe_emit(
            self.__class__.__name__,
            self.progress_update,
            progress,
            message,
        )

    def run(self) -> None:
        success = False
        try:
            if self._is_cancelled():
                return
            executor = ModOperationExecutor(
                self.journal_root, patcher=self._patcher, merger=self._merger
            )
            self.journal = executor.execute(
                self.plan,
                progress=self._on_progress,
                is_cancelled=self._is_cancelled,
            )
            success = not self._is_cancelled()
        except ModOperationCancelledError:
            logger.info("Mod operation plan cancelled; applied files were restored")
        except Exception as error:
            logger.error("Mod operation plan failed: %s", error, exc_info=True)
            _safe_emit(
                self.__class__.__name__,
                self.status_update,
                tr("errors.mod_patching_failed"),
                "error",
            )
        finally:
            _safe_emit(self.__class__.__name__, self.result_ready, success)
