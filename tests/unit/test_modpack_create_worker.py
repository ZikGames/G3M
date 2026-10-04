from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.mod.config import load_mod_config
from workers.modpack_create_worker import CreateModpackThread


class _FailingSignal:
    def emit(self, *_args, **_kwargs):
        raise RuntimeError("receiver deleted")


def _config(mod_id: str) -> dict[str, object]:
    return {
        "config_version": "2.0.0",
        "id": mod_id,
        "name": mod_id,
        "version": "1.0.0",
        "authors": ["Author"],
        "game": "undertale",
        "files": [
            {
                "source": "${mod_path}/payload.txt",
                "target": "${game_path}/data.win",
                "type": "overwrite",
            }
        ],
    }


def test_modpack_create_worker_suppresses_emit_failure_after_error(monkeypatch, tmp_path, caplog):
    worker = CreateModpackThread({}, "Pack", str(tmp_path / "pack"), SimpleNamespace(), SimpleNamespace())
    vars(worker)["status_update"] = _FailingSignal()
    vars(worker)["result_ready"] = _FailingSignal()
    monkeypatch.setattr(worker, "_build_bundle", lambda: (_ for _ in ()).throw(RuntimeError("failed")))

    worker.run()

    assert "CreateModpackThread failed" in caplog.text
    assert "CreateModpackThread: failed to emit" in caplog.text


@pytest.mark.parametrize(
    ("runtime", "data_filename"),
    [("windows", "data.win"), ("linux", "game.unx"), ("macos", "game.ios")],
)
def test_modpack_create_worker_bundles_current_operations(tmp_path, monkeypatch, runtime, data_filename):
    monkeypatch.setattr("workers.modpack_create_worker.resolve_execution_runtime", lambda _: runtime)
    game = tmp_path / "game"
    game.mkdir()
    (game / data_filename).write_text("base", encoding="utf-8")
    mod_root = tmp_path / "mod"
    mod_root.mkdir()
    (mod_root / "payload.txt").write_text("modded", encoding="utf-8")
    mod = SimpleNamespace(id="mod")
    app_state = SimpleNamespace(
        local_config={},
        game_mode=SimpleNamespace(
            executable_type="undertale",
            get_game_path=lambda _config: str(game),
            get_data_path=lambda _config: None,
            get_custom_exec_config_key=lambda: "",
        ),
    )
    mod_service = SimpleNamespace(
        get_mod_config=lambda _mod_id: _config("mod"),
        get_mod_folder_path=lambda _mod_id: str(mod_root),
    )
    output = tmp_path / "pack"
    worker = CreateModpackThread({"global": [mod]}, "Pack", str(output), app_state, mod_service)

    worker._build_bundle()

    config = load_mod_config(output / "mod_config.json")
    assert config["config_version"] == "2.0.0"
    assert isinstance(config["files"], list)
    assert isinstance(config["files"][0], dict)
    assert config["files"][0]["target"] == f"${{game_path}}/{data_filename}"
    assert (output / "payload" / "game-files" / data_filename).read_text("utf-8") == "modded"


@pytest.mark.parametrize(
    ("runtime", "data_filename"),
    [("windows", "data.win"), ("linux", "game.unx"), ("macos", "game.ios")],
)
def test_modpack_preserves_file_hard_overwrite_deletions(tmp_path, monkeypatch, runtime, data_filename):
    monkeypatch.setattr("workers.modpack_create_worker.resolve_execution_runtime", lambda _: runtime)
    game = tmp_path / "game"
    game.mkdir()
    (game / data_filename).write_text("base", encoding="utf-8")
    (game / "obsolete.txt").write_text("obsolete", encoding="utf-8")
    mod_root = tmp_path / "mod"
    mod_root.mkdir()
    (mod_root / "payload.txt").write_text("modded", encoding="utf-8")
    config = _config("mod")
    assert isinstance(config["files"], list)
    assert isinstance(config["files"][0], dict)
    config["files"][0]["type"] = "hard-overwrite"
    app_state = SimpleNamespace(
        local_config={},
        game_mode=SimpleNamespace(
            executable_type="undertale",
            get_game_path=lambda _config: str(game),
            get_data_path=lambda _config: None,
            get_custom_exec_config_key=lambda: "",
        ),
    )
    mod_service = SimpleNamespace(
        get_mod_config=lambda _mod_id: config,
        get_mod_folder_path=lambda _mod_id: str(mod_root),
    )
    output = tmp_path / "pack"

    CreateModpackThread(
        {"global": [SimpleNamespace(id="mod")]},
        "Pack",
        str(output),
        app_state,
        mod_service,
    )._build_bundle()

    package_config = load_mod_config(output / "mod_config.json")
    assert package_config["files"] == [
        {
            "source": "${mod_path}/payload/game-directories/0001/",
            "target": "${game_path}/",
            "type": "hard-extract",
        }
    ]
    assert (output / "payload" / "game-directories" / "0001" / data_filename).read_text(
        encoding="utf-8"
    ) == "modded"
    assert not (output / "payload" / "game-directories" / "0001" / "obsolete.txt").exists()


def test_modpack_uses_the_selected_custom_executable_runtime(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    executable = game / "custom.exe"
    executable.write_bytes(b"")
    (game / "data.win").write_text("base", encoding="utf-8")
    mod_root = tmp_path / "mod"
    mod_root.mkdir()
    (mod_root / "payload.txt").write_text("modded", encoding="utf-8")
    app_state = SimpleNamespace(
        local_config={"custom_executable": str(executable)},
        game_mode=SimpleNamespace(
            executable_type="undertale",
            get_game_path=lambda _config: str(game),
            get_data_path=lambda _config: None,
            get_custom_exec_config_key=lambda: "custom_executable",
        ),
    )
    mod_service = SimpleNamespace(
        get_mod_config=lambda _mod_id: _config("mod"),
        get_mod_folder_path=lambda _mod_id: str(mod_root),
    )
    selected = []
    monkeypatch.setattr(
        "workers.modpack_create_worker.resolve_execution_runtime",
        lambda path: selected.append(path) or "windows",
    )

    CreateModpackThread(
        {"global": [SimpleNamespace(id="mod")]},
        "Pack",
        str(tmp_path / "pack"),
        app_state,
        mod_service,
    )._build_bundle()

    assert selected == [str(executable)]


def test_modpack_cancel_marks_operation_without_a_legacy_patcher(tmp_path):
    worker = CreateModpackThread({}, "Pack", str(tmp_path / "pack"), SimpleNamespace(), SimpleNamespace())

    worker.cancel()

    assert worker._cancelled is True
