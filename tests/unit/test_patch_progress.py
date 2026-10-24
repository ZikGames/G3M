"""Progress inside one patching step comes from the percent G3MTool prints, not only from step counts."""

from __future__ import annotations

import io
import logging
from dataclasses import replace
from types import SimpleNamespace

from adapters.g3mtool_adapter import G3MToolManager
from services.mod_operation_executor import ModOperationExecutor
from services.mod_operation_support import create_g3mtool_merger, create_g3mtool_patcher
from utils.mod.config import MOD_CONFIG_VERSION
from utils.mod.operation_plan import ModPathContext, build_mod_operation_plan

CR = chr(13)
LF = chr(10)


def _config(files):
    return {
        "config_version": MOD_CONFIG_VERSION,
        "id": "test_mod",
        "name": "Test Mod",
        "version": "1.0.0",
        "authors": [],
        "game": "deltarune",
        "files": files,
    }


def _plan(tmp_path, names, *, merged=False):
    mod_root, game_root = tmp_path / "mod", tmp_path / "game"
    mod_root.mkdir()
    game_root.mkdir()
    (game_root / "data.win").write_bytes(b"original")
    for name in names:
        (mod_root / name).write_bytes(b"patch")
    plan = build_mod_operation_plan(
        _config(
            [
                {"source": f"${{mod_path}}/{name}", "target": "${game_path}/data.win", "type": "patch"}
                for name in names
            ]
        ),
        ModPathContext.create(
            mod_path=mod_root,
            game_path=game_root,
            game_data_path=None,
            user_path=tmp_path / "user",
            runtime="windows",
        ),
    )
    if merged:
        plan = type(plan)(
            tuple(replace(op, merge_group=0, merge_priority=index) for index, op in enumerate(plan.operations)),
            (),
        )
    return plan


def _patcher(*percents):
    def patch(_target, _source, output, progress=None):
        for percent in percents:
            if progress:
                progress(percent)
        output.write_bytes(b"patched")
        return True

    return patch


def _collect(tmp_path, plan, **backends):
    reports = []
    ModOperationExecutor(tmp_path / "session", **backends).execute(
        plan,
        step_progress=lambda overall, done, total, operation, percent: reports.append(
            (round(overall, 3), done, total, percent)
        ),
    )
    return reports


def test_a_single_patch_reports_progress_while_it_runs(tmp_path):
    plan = _plan(tmp_path, ["only.g3mpatch"])

    reports = _collect(tmp_path, plan, patcher=_patcher(10, 40, 100))

    assert reports == [(0.0, 0, 1, 0), (0.1, 0, 1, 10), (0.4, 0, 1, 40), (1.0, 0, 1, 100)]


def test_progress_is_shared_between_the_steps_of_a_plan(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch", "b.g3mpatch"])

    reports = _collect(tmp_path, plan, patcher=_patcher(50, 100))

    assert [report[0] for report in reports] == [0.0, 0.25, 0.5, 0.5, 0.75, 1.0]


def test_progress_never_goes_backwards_when_the_tool_starts_a_new_phase(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch"])

    reports = _collect(tmp_path, plan, patcher=_patcher(60, 5, 80))

    assert [report[3] for report in reports] == [0, 60, 60, 80]


def test_a_merged_group_spreads_its_percent_over_all_its_patches(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch", "b.g3mpatch"], merged=True)

    def merge(_target, _patches, output, progress=None):
        assert progress is not None
        progress(50)
        output.write_bytes(b"merged")
        return True

    reports = _collect(tmp_path, plan, merger=merge)

    assert reports == [(0.0, 0, 2, 0), (0.5, 0, 2, 50)]


def test_backends_without_a_progress_parameter_still_work(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch"])

    def plain(_target, _source, output):
        output.write_bytes(b"patched")
        return True

    reports = _collect(tmp_path, plan, patcher=plain)

    assert reports == [(0.0, 0, 1, 0)]


def test_nothing_is_passed_to_backends_when_nobody_asks_for_progress(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch"])
    seen = []

    def patch(_target, _source, output, progress=None):
        seen.append(progress)
        output.write_bytes(b"patched")
        return True

    ModOperationExecutor(tmp_path / "session", patcher=patch).execute(plan)

    assert seen == [None]


class _Tool:
    def __init__(self) -> None:
        self.calls = []

    def is_available(self):
        return True

    def _run(self, name, arguments, progress_callback=None):
        self.calls.append((name, arguments, progress_callback))
        if progress_callback:
            progress_callback(42, "Applying patch")
        return 0, "", ""

    def apply_patch(self, target, patch, output, **keywords):
        return self._run("apply", (target, patch, output), keywords.get("progress_callback"))

    def xpatch_apply(self, target, patch, output, **keywords):
        return self._run("xpatch", (target, patch, output), keywords.get("progress_callback"))

    def merge_patches(self, target, patches, output, **keywords):
        return self._run("merge", (target, patches, output), keywords.get("progress_callback"))


def test_the_real_backends_hand_the_tool_percent_on_when_asked(monkeypatch, tmp_path):
    tool = _Tool()
    monkeypatch.setattr("adapters.g3mtool_adapter.G3MToolManager", lambda *_a, **_k: tool)
    percents = []
    path = tmp_path / "a.g3mpatch"

    create_g3mtool_patcher()(path, path, path, progress=percents.append)
    create_g3mtool_merger()(path, [path], path, progress=percents.append)

    assert percents == [42, 42]
    assert [call[2] is not None for call in tool.calls] == [True, True]


def test_the_real_backends_pass_no_callback_without_a_listener(monkeypatch, tmp_path):
    tool = _Tool()
    monkeypatch.setattr("adapters.g3mtool_adapter.G3MToolManager", lambda *_a, **_k: tool)
    path = tmp_path / "a.xdelta"

    create_g3mtool_patcher()(path, path, path)

    assert tool.calls[0][2] is None


def _stream(*pieces):
    class _Raw:
        def __init__(self) -> None:
            self.pieces = list(pieces)

        def read1(self, _size=-1):
            return self.pieces.pop(0) if self.pieces else b""

    return SimpleNamespace(buffer=_Raw())


def test_each_progress_update_is_seen_as_soon_as_its_carriage_return_arrives():
    manager = object.__new__(G3MToolManager)
    seen, chunks, seen_at_each_read = [], [], []

    class _Raw:
        def __init__(self) -> None:
            self.queue = [
                f"Applying patch: 10%{CR}".encode(),
                f"Applying patch: 20%{CR}".encode(),
                f"Applying patch: 100%{CR}{LF}done{LF}".encode(),
            ]

        def read1(self, _size=-1):
            seen_at_each_read.append(len(seen))
            return self.queue.pop(0) if self.queue else b""

    manager._stream_output(SimpleNamespace(buffer=_Raw()), chunks, lambda percent, label: seen.append((percent, label)))

    assert seen_at_each_read == [0, 1, 2, 3]
    assert seen == [(10, "Applying patch"), (20, "Applying patch"), (100, "Applying patch")]
    assert "".join(chunks) == f"Applying patch: 10%{LF}Applying patch: 20%{LF}Applying patch: 100%{LF}done{LF}"


def test_a_character_split_across_reads_and_a_split_line_ending_are_handled():
    manager = object.__new__(G3MToolManager)
    text = f"Étape: 50%{CR}{LF}fini".encode()
    seen, chunks = [], []
    cut = text.index("É".encode()) + 1  # in the middle of the two-byte letter
    stream = _stream(text[:cut], text[cut : text.index(CR.encode()) + 1], text[text.index(CR.encode()) + 1 :])

    manager._stream_output(stream, chunks, lambda percent, label: seen.append((percent, label)))

    assert seen == [(50, "Étape")]
    assert "".join(chunks) == f"Étape: 50%{LF}fini"


def test_a_last_update_without_a_line_ending_is_still_reported():
    manager = object.__new__(G3MToolManager)
    seen = []

    manager._stream_output(_stream(b"Merging: 99%"), [], lambda percent, _label: seen.append(percent))

    assert seen == [99]


def test_a_real_text_stream_over_bytes_uses_the_same_path():
    manager = object.__new__(G3MToolManager)
    seen = []
    stream = io.TextIOWrapper(io.BytesIO(f"Applying patch: 5%{CR}Applying patch: 6%{CR}".encode()))

    manager._stream_output(stream, [], lambda percent, _label: seen.append(percent))

    assert seen == [5, 6]


def test_progress_milestones_are_logged_for_every_caller(caplog):
    manager = object.__new__(G3MToolManager)
    with caplog.at_level(logging.INFO, logger="adapters.g3mtool_adapter"):
        manager._stream_output(
            _stream(f"Applying patch: 25%{LF}Applying patch: 26%{LF}Applying patch: 50%{LF}".encode()),
            [],
            None,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["G3MTool progress: Applying patch 25%", "G3MTool progress: Applying patch 50%"]


def test_info_entries_are_not_steps(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch"])
    info = replace(plan.operations[0], type="info", target=None)
    plan = type(plan)((info, plan.operations[0], info), ())
    done = []

    ModOperationExecutor(tmp_path / "session", patcher=_patcher()).execute(
        plan, progress=lambda completed, total, operation: done.append((completed, total, operation.type))
    )

    assert done == [(1, 1, "patch")]


def test_a_plan_with_only_info_entries_reports_nothing(tmp_path):
    plan = _plan(tmp_path, ["a.g3mpatch"])
    plan = type(plan)((replace(plan.operations[0], type="info", target=None),), ())
    done = []

    ModOperationExecutor(tmp_path / "session").execute(
        plan,
        progress=lambda *_args: done.append("step"),
        step_progress=lambda *_args: done.append("percent"),
    )

    assert done == []
