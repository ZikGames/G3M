"""A deleted mod that is still remembered as selected must not reach the launch plan."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from services.mod.service import ModManager
from services.mod_operation_support import collect_profile_operation_inputs
from services.used_mods_service import UsedModsManager


def test_deleted_selected_mod_is_remembered_but_not_used(app_state, feedback_service, tmp_path, qapp):
    mods_dir = tmp_path / "mods"
    folder = mods_dir / "Dojo"
    folder.mkdir(parents=True)
    (folder / "mod_config.json").write_text(
        json.dumps({"config_version": "2.0.0", "id": "gb_mod_1", "name": "Dojo", "version": "1.0.0", "authors": ["a"], "game": "deltarune", "files": []}),
        encoding="utf-8",
    )
    app_state.mods_dir = str(mods_dir)
    app_state.current_mode = "normal"
    app_state.local_config["used_mods_deltarune"] = {"deltarune": ["gb_mod_1", "gb_mod_717980"]}
    # the browser list still offers the deleted mod
    app_state.all_mods = [SimpleNamespace(id="gb_mod_717980", name="Acolyte"), SimpleNamespace(id="gb_mod_1", name="Dojo")]

    mod_service = ModManager(app_state, feedback_service)
    service = UsedModsManager(app_state, mod_service, feedback_service, Mock(), parent=None)
    service.parent_widget = Mock()
    service.load_used_mods_state()

    selected = [m for mods in service.used_mods.values() for m in mods]
    ids = [getattr(m, "id", None) or m.get("id") for m in selected]
    assert ids == ["gb_mod_1"]
    inputs = collect_profile_operation_inputs(
        mod_service, ids, game_id="deltarune", game_path=None, game_data_path=None, runtime="windows"
    )
    assert not inputs.findings
    assert service._pending_mod_ids["deltarune"] == ["gb_mod_717980"]
    service.save_used_mods_state()
    assert app_state.local_config["used_mods_deltarune"]["deltarune"] == ["gb_mod_1", "gb_mod_717980"]
