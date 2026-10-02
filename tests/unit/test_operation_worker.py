"""Tests for the non-UI operation patching worker boundary."""

from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

from services.mod_operation_executor import ModOperationCancelledError
from utils.mod.operation_plan import ModOperationPlan, PlannedModOperation


def test_operation_worker_exposes_journal_and_progress(monkeypatch, qapp, tmp_path):
    from workers.mod.operation_worker import ModOperationThread

    operation = PlannedModOperation(
        index=1,
        group_path=(),
        type="info",
        source=tmp_path / "info.txt",
        source_is_directory=False,
        target=None,
        target_is_directory=False,
        source_hash=None,
        target_hash=None,
    )
    journal = SimpleNamespace(state="applied")

    class _Executor:
        def __init__(self, root, *, patcher, merger) -> None:
            assert root == tmp_path / "journal"
            assert callable(patcher)
            assert callable(merger)

        def execute(self, plan, *, progress, is_cancelled):
            assert not is_cancelled()
            progress(1, 1, plan.operations[0])
            return journal

        @staticmethod
        def merge_operations(_operations, _operation) -> tuple[object, ...]:
            return ()

    monkeypatch.setattr("workers.mod.operation_worker.ModOperationExecutor", _Executor)
    worker = ModOperationThread(
        SimpleNamespace(), ModOperationPlan((operation,), ()), tmp_path / "journal"
    )
    progress = []
    messages = []
    results = []
    worker.progress_update.connect(
        lambda value, message: (progress.append(value), messages.append(message))
    )
    worker.result_ready.connect(results.append)

    worker.run()
    qapp.processEvents()

    assert worker.journal is journal
    assert progress == [100]
    assert messages == ["Checking info.txt (1/1)..."]
    assert results == [True]


def test_operation_worker_uses_specific_status_for_each_operation(qapp, tmp_path):
    from workers.mod.operation_worker import ModOperationThread

    worker = ModOperationThread(
        SimpleNamespace(), ModOperationPlan((), ()), tmp_path / "journal"
    )
    messages = []
    worker.progress_update.connect(lambda _value, message: messages.append(message))
    operation_types = (
        "patch",
        "overwrite",
        "soft-overwrite",
        "hard-overwrite",
        "extract",
        "soft-extract",
        "hard-extract",
        "info",
    )
    for index, operation_type in enumerate(operation_types, start=1):
        operation = PlannedModOperation(
            index=index,
            group_path=(),
            type=operation_type,
            source=tmp_path / "source.bin",
            source_is_directory=False,
            target=tmp_path / "target.bin" if operation_type != "info" else None,
            target_is_directory=False,
            source_hash=None,
            target_hash=None,
        )
        worker._on_progress(index, len(operation_types), operation)

    assert [message.split(" ", 1)[0] for message in messages] == [
        "Applying",
        "Copying",
        "Copying",
        "Replacing",
        "Copying",
        "Copying",
        "Replacing",
        "Checking",
    ]
    assert all("target.bin" in message or "source.bin" in message for message in messages)
    assert not any("soft" in message.casefold() or "hard" in message.casefold() for message in messages)


def test_operation_worker_names_patch_format_and_merges_patches(qapp, tmp_path):
    from workers.mod.operation_worker import ModOperationThread

    target = tmp_path / "data.win"
    xdelta = PlannedModOperation(
        index=1,
        group_path=(),
        type="patch",
        source=tmp_path / "first.xdelta",
        source_is_directory=False,
        target=target,
        target_is_directory=False,
        source_hash=None,
        target_hash=None,
        merge_group=0,
        merge_priority=0,
    )
    g3mpatch = replace(
        xdelta,
        index=2,
        source=tmp_path / "second.g3mpatch",
        merge_priority=1,
    )
    worker = ModOperationThread(
        SimpleNamespace(), ModOperationPlan((xdelta, g3mpatch), ()), tmp_path / "journal"
    )
    messages = []
    worker.progress_update.connect(lambda _value, message: messages.append(message))

    worker._on_progress(2, 2, xdelta)

    assert messages == ["Merging 2 patches (XDELTA, G3MPatch) into data.win (2/2)..."]


def test_operation_worker_logs_cancellation_without_traceback(monkeypatch, qapp, tmp_path, caplog):
    from workers.mod.operation_worker import ModOperationThread

    class _Executor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def execute(self, *_args, **_kwargs):
            raise ModOperationCancelledError("operation cancelled")

    monkeypatch.setattr("workers.mod.operation_worker.ModOperationExecutor", _Executor)
    worker = ModOperationThread(
        SimpleNamespace(), ModOperationPlan((), ()), tmp_path / "journal"
    )
    results = []
    worker.result_ready.connect(results.append)
    caplog.set_level(logging.INFO, logger="workers.mod.operation_worker")

    worker.run()
    qapp.processEvents()

    assert results == [False]
    assert "Mod operation plan cancelled; applied files were restored" in caplog.text
    assert "Mod operation plan failed" not in caplog.text
