from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.diagnostics.preflight_service import (
    DiagnosticsPreflightService,
    PreflightFileChange,
    PreflightReport,
    PreflightResourceChange,
    PreflightStepResult,
    export_preflight_report,
)
from services.mod_operation_executor import (
    ModOperationCancelledError,
    ModOperationExecutionError,
)
from utils.mod.operation_plan import ModPathContext, build_profile_operation_plan


@pytest.mark.parametrize("explicit", [False, True])
def test_preflight_cancellation_during_execution_is_not_a_failed_step(tmp_path, monkeypatch, explicit):
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.win").write_text("base", encoding="utf-8")
    mod = tmp_path / "one"
    mod.mkdir()
    (mod / "one.txt").write_text("one", encoding="utf-8")
    service = DiagnosticsPreflightService(SimpleNamespace(local_config={}))

    def cancel_execution(*args, **kwargs):
        if explicit:
            raise ModOperationCancelledError("Cancelled")
        service.cancel()
        raise ModOperationExecutionError("Backend terminated")

    monkeypatch.setattr("services.diagnostics.preflight_service.ModOperationExecutor.execute", cancel_execution)
    report = service.run(_plan(game, mod), str(game))

    assert report.cancelled
    assert not report.success
    assert report.steps == ()
    assert (game / "data.win").read_text(encoding="utf-8") == "base"


def _report() -> PreflightReport:
    return PreflightReport(
        success=True,
        cancelled=False,
        duration_seconds=1.25,
        steps=(PreflightStepResult("group", 1, ("mod_a",), True, 0.5),),
        resources=(
            PreflightResourceChange(
                "group", 1, "File", "changed", "code.gml", ("mod_a",)
            ),
        ),
        files=(
            PreflightFileChange("game/data.win", "modified", 10, 12, "before", "after"),
        ),
        issues=("warning <details>",),
    )


def _config(mod_id: str, source: str, target: str) -> dict[str, object]:
    return {
        "config_version": "2.0.0",
        "id": mod_id,
        "name": mod_id,
        "version": "1.0.0",
        "authors": ["Author"],
        "game": "undertale",
        "files": [{"source": source, "target": target, "type": "overwrite"}],
    }


def _plan(game: Path, *mod_roots: Path, runtime: str | None = "windows"):
    configs = {
        root.name: _config(root.name, f"${{mod_path}}/{root.name}.txt", "${game_path}/data.win")
        for root in mod_roots
    }
    contexts = {
        root.name: ModPathContext.create(
            mod_path=root,
            game_path=game,
            game_data_path=None,
            user_path=game.parent / "user",
            runtime=runtime,
        )
        for root in mod_roots
    }
    return build_profile_operation_plan(configs, contexts, tuple(root.name for root in mod_roots))


def test_preflight_report_serialization_is_deterministic():
    report = _report()

    assert report.to_dict() == report.to_dict()
    assert report.to_dict()["resources"][0]["name"] == "code.gml"


def test_preflight_export_writes_equivalent_json_and_safe_html(tmp_path):
    report = _report()
    target = tmp_path / "diagnostics.html"

    html_path, json_path = export_preflight_report(report, str(target))

    payload = json.loads((tmp_path / "diagnostics.json").read_text("utf-8"))
    html = target.read_text("utf-8")
    assert payload == report.to_dict()
    assert html_path == str(target)
    assert json_path == str(tmp_path / "diagnostics.json")
    assert "warning &lt;details&gt;" in html
    assert "<script>" not in html


def test_preflight_executes_current_operations_in_order_without_changing_game(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.win").write_text("base", encoding="utf-8")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "first.txt").write_text("first", encoding="utf-8")
    (second / "second.txt").write_text("second", encoding="utf-8")

    report = DiagnosticsPreflightService(SimpleNamespace(local_config={})).run(
        _plan(game, first, second), str(game), user_path=str(tmp_path / "user")
    )

    assert report.success is True
    assert [step.mod_ids for step in report.steps] == [("first",), ("second",)]
    assert (game / "data.win").read_text("utf-8") == "base"
    assert any(change.relative_path == "game/data.win" for change in report.files)


def test_preflight_cancelled_before_run_does_not_stage_files(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    source = tmp_path / "one"
    source.mkdir()
    (source / "one.txt").write_text("one", encoding="utf-8")
    service = DiagnosticsPreflightService(SimpleNamespace(local_config={}))
    service.cancel()

    report = service.run(_plan(game, source), str(game))

    assert report.cancelled is True
    assert report.success is False


def test_preflight_stages_a_custom_target_without_writing_it(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("payload", encoding="utf-8")
    custom_target = tmp_path / "outside.txt"
    config = _config("source", "${mod_path}/payload.txt", custom_target.as_posix())
    plan = build_profile_operation_plan(
        {"source": config},
        {
            "source": ModPathContext.create(
                mod_path=source,
                game_path=game,
                game_data_path=None,
                user_path=tmp_path / "user",
            )
        },
        ("source",),
    )

    report = DiagnosticsPreflightService(SimpleNamespace(local_config={})).run(plan, str(game))

    assert report.success is True
    assert not custom_target.exists()
    assert any("Custom targets are staged in isolation" in issue for issue in report.issues)
    assert any(change.relative_path.startswith("custom/") for change in report.files)


@pytest.mark.parametrize("archived", [False, True])
def test_preflight_later_sources_read_staged_custom_outputs(tmp_path, archived):
    import zipfile

    game = tmp_path / "game"
    mod = tmp_path / "mod"
    game.mkdir()
    mod.mkdir()
    extension = ".zip" if archived else ".txt"
    custom = tmp_path / f"outside{extension}"
    source = mod / f"payload{extension}"
    if archived:
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("payload.txt", "replacement")
        with zipfile.ZipFile(custom, "w") as archive:
            archive.writestr("payload.txt", "original")
    else:
        source.write_text("replacement", encoding="utf-8")
        custom.write_text("original", encoding="utf-8")
    previous = custom.read_bytes()
    config = _config("mod", f"${{mod_path}}/payload{extension}", custom.as_posix())
    assert isinstance(config["files"], list)
    config["files"].append({
        "source": custom.as_posix() + ("/payload.txt" if archived else ""),
        "target": "${game_path}/result.txt", "type": "overwrite",
    })
    plan = build_profile_operation_plan(
        {"mod": config}, {"mod": ModPathContext.create(
            mod_path=mod, game_path=game, game_data_path=None, user_path=tmp_path / "user",
        )}, ("mod",),
    )

    report = DiagnosticsPreflightService(SimpleNamespace(local_config={})).run(plan, str(game))

    assert report.success, report.issues
    assert custom.read_bytes() == previous
    assert not (game / "result.txt").exists()
    expected_hash = sha256(b"replacement").hexdigest()
    assert any(change.relative_path == "game/result.txt" and change.after_hash == expected_hash for change in report.files)


def test_preflight_hard_operations_clear_the_parent_once_and_report_removed_siblings(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    sibling = game / "original.txt"
    sibling.write_text("original", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    for name in ("first.txt", "second.txt"):
        (source / name).write_text(name, encoding="utf-8")
    config = _config("source", "${mod_path}/first.txt", "${game_path}/first.txt")
    config["files"] = [
        {"source": f"${{mod_path}}/{name}", "target": f"${{game_path}}/{name}", "type": "hard-overwrite"}
        for name in ("first.txt", "second.txt")
    ]
    context = ModPathContext.create(
        mod_path=source, game_path=game, game_data_path=None, user_path=tmp_path / "user",
    )
    plan = build_profile_operation_plan({"source": config}, {"source": context}, ("source",))

    report = DiagnosticsPreflightService(SimpleNamespace(local_config={})).run(plan, str(game))

    assert report.success
    changes = [(change.relative_path, change.operation) for change in report.files]
    assert ("game/original.txt", "removed") in changes
    assert ("game/first.txt", "added") in changes
    assert ("game/second.txt", "added") in changes
    assert ("game/first.txt", "removed") not in changes
    assert sibling.read_text(encoding="utf-8") == "original"
    assert not (game / "first.txt").exists()
    assert not (game / "second.txt").exists()
