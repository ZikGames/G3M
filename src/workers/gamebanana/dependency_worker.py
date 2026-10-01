"""Resolve safe GameBanana downloads for missing operation dependencies."""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal

from adapters.gamebanana_adapter import GameBananaAPI
from ui.utils.thread_lifetime import ManagedQThread
from ui.utils.thread_lifetime import safe_emit as _safe_emit

logger = logging.getLogger(__name__)


class ResolveGameBananaDependenciesThread(ManagedQThread):
    """Resolve download records off the UI thread; Downloads performs the install."""

    resolved = pyqtSignal(object, object)

    def __init__(self, dependency_ids: set[str], game: str, parent=None) -> None:
        super().__init__(parent)
        self._dependency_ids = tuple(sorted(dependency_ids))
        self._game = game
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        resolved: dict[str, dict] = {}
        failures: dict[str, str] = {}
        try:
            api = GameBananaAPI()
            for dependency_id in self._dependency_ids:
                if self._cancelled or self.isInterruptionRequested():
                    return
                result = api.resolve_dependency_download(dependency_id, self._game)
                if result:
                    resolved[dependency_id] = result
                else:
                    failures[dependency_id] = "No compatible GameBanana download is available."
        except Exception as error:
            logger.warning("Could not resolve GameBanana dependencies: %s", error, exc_info=True)
            failures.update(
                {dependency_id: str(error) for dependency_id in self._dependency_ids if dependency_id not in resolved}
            )
        _safe_emit(self.__class__.__name__, self.resolved, resolved, failures)
