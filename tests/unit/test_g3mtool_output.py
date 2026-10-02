"""Regression coverage for the current patch backend adapter."""

import io
from unittest.mock import Mock

import pytest

from adapters.g3mtool_adapter import G3MToolManager


@pytest.mark.parametrize("fail_callback", [False, True])
def test_output_collection_preserves_text_after_callback_error(fail_callback):
    manager = object.__new__(G3MToolManager)
    text = "Applying patch: 10%\rApplying patch: 50%\n" + "output\n" * 10_000 + "Applying patch: 100%"
    stream = io.StringIO(text)
    chunks = []
    callback = Mock(side_effect=RuntimeError("closed view") if fail_callback else None)

    manager._stream_output(stream, chunks, callback)

    assert "".join(chunks) == text
    assert stream.read() == ""
    assert callback.call_count == (1 if fail_callback else 3)


def test_execute_separates_child_arguments_from_host_options(monkeypatch):
    manager = G3MToolManager(Mock(local_config={"custom_xdelta_path": "delta.exe"}))
    monkeypatch.setattr(manager, "refresh_executable", lambda: "G3MTool.exe")
    manager._run = Mock(return_value=(0, "", ""))

    manager.execute("script.csx", ["--output", "payload"], output_path="actual.win")

    assert manager._run.call_args.args[0] == [
        "G3MTool.exe",
        "execute",
        "script.csx",
        "--output",
        "actual.win",
        "--xdelta-path",
        "delta.exe",
        "--",
        "--output",
        "payload",
    ]


@pytest.mark.parametrize(
    ("method_name", "paths", "action"),
    [
        ("batch_apply_patches", ["one.g3mpatch"], "apply"),
        ("batch_create_patches", ["modified.win"], "create"),
    ],
)
def test_batch_commands_keep_their_original_arguments(method_name, paths, action):
    manager = object.__new__(G3MToolManager)
    manager._run_command = Mock(return_value=(0, "", ""))

    getattr(manager, method_name)(
        "original.win",
        paths,
        "output",
        continue_on_error=True,
        include_xdelta_fallback=True,
    )

    assert manager._run_command.call_args.args[0] == [
        "patch",
        "batch",
        action,
        "original.win",
        *paths,
        "--out-dir",
        "output",
        "--continue-on-error",
        "--xdelta-fallback",
    ]


@pytest.mark.parametrize(
    ("method_name", "input_path", "action"),
    [
        ("xpatch_apply", "patch.xdelta", "apply"),
        ("xpatch_create", "modified.win", "create"),
    ],
)
def test_xpatch_commands_keep_their_original_arguments(
    method_name, input_path, action
):
    manager = object.__new__(G3MToolManager)
    manager._run_command = Mock(return_value=(0, "", ""))

    getattr(manager, method_name)("original.win", input_path, "output.win")

    assert manager._run_command.call_args.args[0] == [
        "xpatch",
        action,
        "original.win",
        input_path,
        "output.win",
    ]
