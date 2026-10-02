"""Regression tests for operation diagnostics dialog inputs."""

from types import SimpleNamespace

from ui.dialogs.mod_diagnostics_dialog import ModDiagnosticsDialog


def _config(mod_id: str) -> dict[str, object]:
    return {
        "config_version": "2.0.0",
        "id": mod_id,
        "name": mod_id,
        "version": "1.0.0",
        "authors": ["Author"],
        "game": "deltarune",
        "files": [],
    }


def test_operation_diagnostics_inputs_keep_profile_step_order(tmp_path):
    first = SimpleNamespace(id="first")
    second = SimpleNamespace(id="second")
    mod_paths = {mod.id: tmp_path / mod.id for mod in (first, second)}
    for path in mod_paths.values():
        path.mkdir()
    game_path = tmp_path / "game"
    game_path.mkdir()
    game_mode = SimpleNamespace(
        default_tab_id="chapter",
        get_game_path=lambda _config: str(game_path),
        get_data_path=lambda _config: None,
    )
    dialog = ModDiagnosticsDialog.__new__(ModDiagnosticsDialog)
    dialog._app_state = SimpleNamespace(
        game_mode=game_mode,
        local_config={},
        current_mode="chapter",
        selected_chapter_id="chapter",
    )
    dialog._mod_service = SimpleNamespace(
        get_mod_config=lambda mod_id: _config(mod_id),
        get_mod_folder_path=lambda mod_id: str(mod_paths[mod_id]),
    )
    dialog._used_mods_service = SimpleNamespace(
        get_mod_steps=lambda _chapter: [[second, first]]
    )

    result = dialog._operation_report_inputs({"chapter": [first, second]})

    assert result is not None
    configs, steps, contexts = result
    assert list(configs) == ["first", "second"]
    assert steps == [["second", "first"]]
    assert contexts["first"].game_path == game_path
    assert contexts["second"].mod_path == mod_paths["second"]


def test_operation_diagnostics_uses_selected_executable_runtime(tmp_path, monkeypatch):
    mod = SimpleNamespace(id="mod")
    mod_path = tmp_path / "mod"
    mod_path.mkdir()
    game_path = tmp_path / "game"
    game_path.mkdir()
    executable = game_path / "custom.exe"
    executable.write_bytes(b"")
    game_mode = SimpleNamespace(
        default_tab_id="chapter",
        executable_type="deltarune",
        get_game_path=lambda _config: str(game_path),
        get_data_path=lambda _config: None,
        get_custom_exec_config_key=lambda: "custom_executable",
    )
    dialog = ModDiagnosticsDialog.__new__(ModDiagnosticsDialog)
    dialog._app_state = SimpleNamespace(
        game_mode=game_mode,
        local_config={"custom_executable": str(executable)},
        current_mode="chapter",
        selected_chapter_id="chapter",
    )
    dialog._mod_service = SimpleNamespace(
        get_mod_config=lambda _mod_id: _config("mod"),
        get_mod_folder_path=lambda _mod_id: str(mod_path),
    )
    dialog._used_mods_service = SimpleNamespace(
        get_mod_steps=lambda _chapter: [[mod]]
    )
    selected = []
    monkeypatch.setattr(
        "ui.dialogs.mod_diagnostics_dialog.resolve_execution_runtime",
        lambda path: selected.append(path) or "windows",
    )

    result = dialog._operation_report_inputs({"chapter": [mod]})

    assert result is not None
    assert selected == [str(executable)]
    assert result[2]["mod"].runtime == "windows"


def test_preflight_operation_phase_uses_localized_step_text():
    from services.localization_service import tr

    dialog = ModDiagnosticsDialog.__new__(ModDiagnosticsDialog)
    values = []
    phases = []
    dialog._preflight_progress = SimpleNamespace(setValue=values.append)
    dialog._preflight_phase = SimpleNamespace(setText=phases.append)

    dialog._on_preflight_progress(50, "patching_step:global:2:5")

    assert values == [50]
    assert phases == [tr("diagnostics.actual_phase_step", section="global", step="2", total="5")]
