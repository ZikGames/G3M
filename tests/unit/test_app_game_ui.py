from types import SimpleNamespace

from PyQt6.QtWidgets import QCheckBox

from app.game_ui import update_portproton_ui, update_steam_launch_checkbox_state


def test_steam_launch_stays_saved_but_is_disabled_without_steam_app(qapp):
    steam_checkbox = QCheckBox()
    steam_checkbox.setChecked(True)
    portproton_checkbox = QCheckBox()
    window = SimpleNamespace(
        app_state=SimpleNamespace(
            local_config={"launch_via_steam": True},
            game_mode=SimpleNamespace(
                steam_app_id="", block_steam_with_direct_launch=False
            ),
            current_mode="normal",
        ),
        launch_via_steam_checkbox=steam_checkbox,
        use_portproton_checkbox=portproton_checkbox,
    )

    update_steam_launch_checkbox_state(window)
    update_portproton_ui(window)

    assert window.app_state.local_config["launch_via_steam"] is True
    assert steam_checkbox.isChecked()
    assert not steam_checkbox.isEnabled()
    assert portproton_checkbox.isEnabled()

    window.app_state.game_mode.steam_app_id = "1690940"
    update_steam_launch_checkbox_state(window)
    update_portproton_ui(window)

    assert steam_checkbox.isChecked()
    assert steam_checkbox.isEnabled()
    assert not portproton_checkbox.isEnabled()
