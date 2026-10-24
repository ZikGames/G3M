"""Tests for runtime game registry service."""

import pytest

from models.game_modes import (
    BUILTIN_GAME_REGISTRY,
    GameEntry,
    get_game,
    replace_game_entries,
)
from services.game_registry_service import (
    GameRegistryService,
    GameRegistryValidationError,
)
from services.localization_service import localization_service
from services.settings_service import SettingsManager


@pytest.fixture
def registry_service(app_state, feedback_service):
    settings_service = SettingsManager(
        app_state, feedback_service, localization_service, parent=None
    )
    app_state.local_config = {}
    service = GameRegistryService(app_state, settings_service, parent=None)
    service.load()
    yield service
    replace_game_entries(
        [
            GameEntry(
                id=game_id,
                is_builtin=True,
                is_visible=True,
                sort_index=index,
                game_definition=game,
            )
            for index, (game_id, game) in enumerate(BUILTIN_GAME_REGISTRY.items())
        ]
    )


def test_create_custom_game_registers_runtime_definition(registry_service):
    """Checks that creating custom game registers runtime definition."""
    record = registry_service.create_custom_game(
        display_name="My Game",
        primary_executable="my_game.exe",
        data_file_name="data.win",
        steam_app_id="123456",
        gamebanana_id="9876",
    )
    game = get_game(record.id)
    assert game is not None
    assert game.game_id == record.id
    assert game.steam_app_id == "123456"
    assert game.gamebanana_id == 9876
    assert any(entry.id == record.id for entry in registry_service.list_visible_games())


def test_create_custom_game_accepts_gamebanana_game_url(registry_service):
    record = registry_service.create_custom_game(
        display_name="Linked Game",
        primary_executable="linked.exe",
        data_file_name="data.win",
        gamebanana_id="https://gamebanana.com/games/9876",
    )

    assert record.gamebanana_id == 9876


def test_cannot_hide_last_visible_game(registry_service):
    """Checks that cannoting hide last visible game."""
    visible_ids = [entry.id for entry in registry_service.list_visible_games()]
    for game_id in visible_ids[1:]:
        registry_service.set_visibility(game_id, False)
    with pytest.raises(GameRegistryValidationError):
        registry_service.set_visibility(visible_ids[0], False)


def test_search_games_include_only_visible_searchable_entries(registry_service):
    """Checks that searching games include only visible searchable entries."""
    custom = registry_service.create_custom_game(
        display_name="Searchable Game",
        primary_executable="search.exe",
        data_file_name="data.win",
        gamebanana_id="7777",
    )
    registry_service.set_visibility("undertale", False)
    search_ids = [entry.id for entry in registry_service.list_search_games()]
    assert custom.id in search_ids
    assert "undertale" not in search_ids
    assert "deltarunedemo" not in search_ids


def test_custom_game_uses_explicit_executable_and_data_file(registry_service):
    """Checks that customing game uses explicit executable and data file."""
    record = registry_service.create_custom_game(
        display_name="Platform Test",
        primary_executable="platform_game.exe",
        data_file_name="game.unx",
    )
    game = get_game(record.id)
    assert game is not None
    assert game.get_executable_candidates("windows")[0] == "platform_game.exe"
    assert game.get_executable_candidates("linux")[0] == "platform_game.exe"
    assert game.get_executable_candidates("mac")[0] == "platform_game.exe"
    assert game.data_file_name == "game.unx"


def test_custom_game_load_defaults_linux_native_data_file_name(registry_service):
    """Checks that Linux-native custom games default to game.unx."""
    registry_service.settings_service.read_json = lambda _path: {
        "custom_games": [
            {
                "id": "custom_native",
                "display_name": "Native GameMaker Game",
                "primary_executable": "runner",
            }
        ]
    }

    registry_service.load()

    game = get_game("custom_native")
    assert game is not None
    assert game.data_file_name == "game.unx"


def test_custom_game_load_keeps_windows_exe_default_data_win(registry_service):
    """Checks that Windows custom games keep data.win as the default data file."""
    registry_service.settings_service.read_json = lambda _path: {
        "custom_games": [
            {
                "id": "custom_windows",
                "display_name": "Windows GameMaker Game",
                "primary_executable": "game.exe",
            }
        ]
    }

    registry_service.load()

    game = get_game("custom_windows")
    assert game is not None
    assert game.data_file_name == "data.win"


def test_custom_game_can_take_the_id_a_mod_names(registry_service):
    record = registry_service.create_custom_game(
        display_name="Hotline Game",
        primary_executable="hotline.exe",
        data_file_name="data.win",
        game_id="hotline-game",
    )

    assert record.id == "hotline-game"
    assert get_game("hotline-game") is not None


@pytest.mark.parametrize(
    ("game_id", "error"),
    [
        ("Has Caps", "games.validation_id_invalid"),
        ("self", "games.validation_id_invalid"),
        ("1game", "games.validation_id_invalid"),
        ("deltarune", "games.validation_id_taken"),
    ],
)
def test_custom_game_id_must_be_valid_and_free(registry_service, game_id, error):
    with pytest.raises(GameRegistryValidationError) as caught:
        registry_service.create_custom_game(
            display_name="Other", primary_executable="o.exe", data_file_name="data.win", game_id=game_id
        )

    assert caught.value.key == error


def test_registry_reports_whether_an_id_is_free_for_a_new_game(registry_service):
    assert registry_service.is_valid_new_game_id("brand-new")
    assert not registry_service.is_valid_new_game_id("deltarune")
    assert not registry_service.is_valid_new_game_id("Bad Id")


def _custom(registry_service, game_id=""):
    return registry_service.create_custom_game(
        display_name="Hotline Game",
        primary_executable="hotline.exe",
        data_file_name="data.win",
        game_id=game_id,
    )


def test_editing_a_custom_game_can_rename_its_id(registry_service):
    record = _custom(registry_service, "hotline")
    renamed = []
    registry_service.game_renamed.connect(lambda old, new: renamed.append((old, new)))

    registry_service.update_custom_game(
        "hotline", "Hotline Game", "hotline.exe", "data.win", game_id="hotline-2"
    )

    assert renamed == [("hotline", "hotline-2")]
    assert get_game("hotline") is None
    assert get_game("hotline-2") is not None
    assert "hotline-2" in registry_service.state.order
    assert "hotline" not in registry_service.state.order
    assert registry_service.is_visible("hotline-2")
    assert record.id == "hotline"


def test_editing_a_custom_game_keeps_its_id_when_none_is_given(registry_service):
    _custom(registry_service, "hotline")
    registry_service.game_renamed.connect(lambda *_: pytest.fail("renamed"))

    registry_service.update_custom_game("hotline", "Hotline Game 2", "hotline.exe", "data.win")
    registry_service.update_custom_game(
        "hotline", "Hotline Game 3", "hotline.exe", "data.win", game_id="hotline"
    )

    assert get_game("hotline") is not None


def test_renaming_a_custom_game_cannot_take_a_built_in_id(registry_service):
    _custom(registry_service, "hotline")

    with pytest.raises(GameRegistryValidationError) as caught:
        registry_service.update_custom_game(
            "hotline", "Hotline Game", "hotline.exe", "data.win", game_id="deltarune"
        )

    assert caught.value.key == "games.validation_id_taken"
    assert get_game("hotline") is not None
