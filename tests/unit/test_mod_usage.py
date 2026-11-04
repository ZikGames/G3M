"""A mod that a running operation uses cannot be deleted."""

import json

import pytest

from models.exceptions import AppError
from services.mod.service import ModManager
from services.mod_operation_executor import ModOperationExecutor
from services.mod_usage import hold_mods, is_mod_busy
from utils.mod.operation_plan import ModOperationPlan, PlannedModOperation


def test_holds_nest_and_release():
    with hold_mods(["a", None]):
        with hold_mods(["a"]):
            assert is_mod_busy("a")
        assert is_mod_busy("a")
        assert not is_mod_busy("b")
    assert not is_mod_busy("a")


def test_delete_is_refused_while_the_mod_is_held(app_state, feedback_service, tmp_path):
    folder = tmp_path / "Dojo"
    folder.mkdir()
    (folder / "mod_config.json").write_text(
        json.dumps({"config_version": "2.0.0", "id": "gb_mod_1", "name": "Dojo", "version": "1.0.0", "authors": ["a"], "game": "deltarune", "files": []}),
        encoding="utf-8",
    )
    app_state.mods_dir = str(tmp_path)
    service = ModManager(app_state, feedback_service)
    mod = {"id": "gb_mod_1", "name": "Dojo", "folder_path": str(folder)}

    with hold_mods(["gb_mod_1"]), pytest.raises(AppError):
        service.delete_mod_files(mod)
    assert folder.is_dir()

    service.delete_mod_files(mod)
    assert not folder.exists()


def test_executor_holds_the_mods_of_its_plan(tmp_path):
    source, target = tmp_path / "p.xdelta", tmp_path / "data.win"
    source.write_bytes(b"p")
    target.write_bytes(b"d")
    operation = PlannedModOperation(0, (), "patch", source, False, target, False, None, None, mod_id="gb_mod_9")
    seen = []

    def patcher(_target, _patch, output):
        seen.append(is_mod_busy("gb_mod_9"))
        output.write_bytes(b"x")
        return True

    ModOperationExecutor(tmp_path / "journal", patcher=patcher).execute(ModOperationPlan((operation,), ()))

    assert seen == [True]
    assert not is_mod_busy("gb_mod_9")


def test_an_operation_waits_for_a_running_deletion():
    import threading
    import time

    from services.mod_usage import reserve_for_deletion

    entered = threading.Event()

    def operation():
        with hold_mods(["gb_mod_5"]):
            entered.set()

    with reserve_for_deletion(["gb_mod_5"]):
        worker = threading.Thread(target=operation)
        worker.start()
        time.sleep(0.3)
        assert not entered.is_set()
    worker.join(timeout=5)
    assert entered.is_set()


def test_deletion_is_refused_while_held_and_while_already_deleting():
    from services.mod_usage import ModInUseError, reserve_for_deletion

    with hold_mods(["gb_mod_6"]), pytest.raises(ModInUseError), reserve_for_deletion(["gb_mod_6"]):
        pass
    with reserve_for_deletion(["gb_mod_6"]), pytest.raises(ModInUseError), reserve_for_deletion(["gb_mod_6"]):
        pass
    assert not is_mod_busy("gb_mod_6")

