"""Tag mapping applied while migrating configs."""

from __future__ import annotations

from utils.mod.legacy_config_migration import migrate_legacy_config


def test_current_config_tags_are_mapped_and_deduplicated():
    migrated = migrate_legacy_config(
        {
            "config_version": "2.0.0",
            "id": "tagged",
            "name": "Tagged",
            "version": "1.0.0",
            "authors": ["a"],
            "game": "deltarune",
            "files": [],
            "tags": ["tag_1", "tag_2", "Translation", "Translation"],
        }
    )

    assert migrated["tags"] == ["other", "textedit"]


def test_legacy_config_without_recognized_tags_drops_none():
    migrated = migrate_legacy_config({"id": "plain", "name": "Plain", "tags": []})

    assert "tags" not in migrated


def test_game_specific_tags_survive_migration():
    migrated = migrate_legacy_config(
        {"id": "special", "name": "Special", "tags": ["cyop afom", "fb_addon", "CYOP/AFOM"]}
    )

    assert migrated["tags"] == ["CYOP/AFOM", "FB-Addon"]
