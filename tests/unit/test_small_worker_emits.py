"""Unit tests for small worker signal crash handling."""

from __future__ import annotations

from helpers import FailingSignal

from workers.background_loader_worker import BgLoader
from workers.changelog_worker import FetchChangelogWorker


def test_background_loader_suppresses_emit_failure(caplog):
    worker = BgLoader("missing.png")
    vars(worker)["loaded"] = FailingSignal()

    worker.run()

    assert "BgLoader: failed to emit" in caplog.text


def test_changelog_worker_suppresses_emit_failure(caplog):
    worker = FetchChangelogWorker("plain changelog")
    vars(worker)["finished"] = FailingSignal()

    worker.run()

    assert "FetchChangelogWorker: failed to emit" in caplog.text
