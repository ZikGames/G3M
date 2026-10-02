import logging

from services.migration_service import migrate_legacy_profile_mods


def test_profile_migration_renames_colliding_mod(tmp_path):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    legacy.mkdir()
    target.mkdir()
    (legacy / "mod").mkdir()
    (target / "mod").mkdir()
    (legacy / "mods_data.json").write_text("{}", encoding="utf-8")
    (target / "mods_data.json").write_text("not json", encoding="utf-8")

    migrated = migrate_legacy_profile_mods(legacy, target, logging.getLogger(__name__))

    assert migrated
    assert not (legacy / "mod").exists()
    assert (target / "mod").is_dir()
    assert (target / "mod_1").is_dir()
    assert (legacy / "mods_data.json").is_file()
    assert not (target / "mods_data_1.json").exists()
