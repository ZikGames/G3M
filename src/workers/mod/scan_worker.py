"""Worker thread for scanning mod directories."""

import logging

from PyQt6.QtCore import pyqtSignal

from ui.utils.thread_lifetime import ManagedQThread
from utils.mod.scan_utils import scan_mods_directory

logger = logging.getLogger(__name__)


def _safe_emit_scan_completed(worker, result: dict) -> None:
    try:
        worker.scan_completed.emit(result)
    except Exception as e:
        logger.error(
            f"ModScanThread: failed to emit scan_completed signal: {e}",
            exc_info=True,
        )


class ModScanThread(ManagedQThread):
    """Background thread for scanning mod directory."""

    scan_completed = pyqtSignal(dict)

    def __init__(self, mods_dir: str, parent=None) -> None:
        super().__init__(parent)
        self.mods_dir = mods_dir
        self._app_state = getattr(parent, "app_state", None)
        self._cancel_flag = False

    def cancel(self):
        self._cancel_flag = True

    def run(self):
        try:
            if self._app_state is not None:
                app_state = self._app_state
                if hasattr(app_state, "_scan_blocked") and app_state._scan_blocked:
                    logger.debug("ModScanThread: Scan blocked during installation")
                    _safe_emit_scan_completed(self, {})
                    return
        except Exception as e:
            logger.debug(f"ModScanThread: Could not check scan block status: {e}")
        try:
            cache, _ = scan_mods_directory(
                self.mods_dir,
                is_cancelled=lambda: self._cancel_flag or self.isInterruptionRequested(),
            )
            result = {
                mod_id: {
                    "id": info.id,
                    "folder_path": info.folder_path,
                    "folder_name": info.folder_name,
                    "config_data": info.config_data,
                    "config_mtime": info.config_mtime,
                    "config_digest": info.config_digest,
                }
                for mod_id, info in cache.items()
            }
        except Exception as error:
            logger.error("ModScanThread: failed to scan %s: %s", self.mods_dir, error)
            result = {}
        _safe_emit_scan_completed(self, result)
