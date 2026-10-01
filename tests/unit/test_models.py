"""Tests for current mod metadata and game section filtering."""

import pytest

from models.game_modes import DeltaruneDemoGame, DeltaruneGame, GameTab, get_all_games
from models.mod_models import BrowserModInfo, LocalModInfo


def _local_mod(*, game="deltarune", sections=frozenset({"deltarune_1"})):
    return LocalModInfo(
        id="local_mod",
        name="Local Mod",
        version="1.0.0",
        authors=["Author"],
        description="Description",
        game=game,
        sections=sections,
    )


def test_local_mod_exposes_sections_from_current_config():
    mod = LocalModInfo.from_dict(
        {
            "config_version": "2.0.0",
            "id": "local_mod",
            "name": "Local Mod",
            "version": "1.0.0",
            "authors": ["Author"],
            "game": "deltarune",
            "files": [
                {
                    "source": "${mod_path}/data.xdelta",
                    "target": "${game_path}/chapter1_windows/data.win",
                    "type": "patch",
                }
            ],
        }
    )

    assert mod.sections == frozenset({"deltarune_1"})
    assert mod.supports_section("deltarune_1")
    assert not mod.supports_section("deltarune_2")


def test_remote_listing_is_not_section_limited_before_import():
    mod = BrowserModInfo.from_dict({"id": "gb_mod_1", "game": "deltarune"})

    assert mod.sections is None
    assert mod.supports_section("deltarune_5")
    assert mod.is_gamebanana_mod()


def test_game_filters_use_operation_sections():
    game = DeltaruneGame()
    visible = _local_mod()
    other_section = _local_mod(sections=frozenset({"deltarune_2"}))

    filtered = game.filter_mods_for_ui([visible, other_section])

    assert visible in filtered[1]
    assert other_section not in filtered[1]


def test_demo_filter_requires_demo_operations():
    game = DeltaruneDemoGame()
    demo = _local_mod(game="deltarunedemo", sections=frozenset({"deltarunedemo"}))
    other = _local_mod(game="deltarune", sections=frozenset({"deltarunedemo"}))

    assert game.filter_mods_for_ui([demo, other])[0] == [demo]


def test_game_tab_is_immutable_and_registry_is_populated():
    tab = GameTab("deltarune_1", "1", "tabs.chapter_1")

    with pytest.raises(AttributeError):
        tab.tab_id = "other"

    assert {game.game_id for game in get_all_games()} >= {"deltarune", "undertale"}
