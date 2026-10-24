"""Choosing which saved versions are exported with, or installed from, a mod package."""

import json
import os
import zipfile

import pytest

from utils.mod.version_package import (
    VersionSelection,
    apply_version_selection,
    export_mod_archive,
    resolve_package_versions,
)
from utils.mod.version_utils import create_version_zip, list_local_versions


def _write_mod(folder, version, payload, mod_id="local_pack"):
    os.makedirs(folder, exist_ok=True)
    config = {"id": mod_id, "name": "Pack", "version": version, "game": "deltarune", "files": {}}
    with open(os.path.join(folder, "mod_config.json"), "w", encoding="utf-8") as handle:
        json.dump(config, handle)
    with open(os.path.join(folder, "data.txt"), "w", encoding="utf-8") as handle:
        handle.write(payload)


@pytest.fixture
def mod_dir(tmp_path):
    folder = str(tmp_path / "pack")
    _write_mod(folder, "3.0", "three")
    for name, payload in (("1.0", "one"), ("2.0", "two")):
        staged = str(tmp_path / f"stage_{name}")
        _write_mod(staged, name, payload)
        create_version_zip(staged, folder, name)
    return folder


def _names(path):
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def test_export_without_versions_has_only_the_active_files(mod_dir, tmp_path):
    export_mod_archive(mod_dir, str(tmp_path / "out.zip"))

    assert _names(str(tmp_path / "out.zip")) == {"mod_config.json", "data.txt"}


def test_export_keeps_only_the_named_versions(mod_dir, tmp_path):
    export_mod_archive(mod_dir, str(tmp_path / "out.zip"), {"2.0"})

    assert _names(str(tmp_path / "out.zip")) == {"mod_config.json", "data.txt", "mod_versions/2.0.zip"}


def test_export_with_every_version(mod_dir, tmp_path):
    export_mod_archive(mod_dir, str(tmp_path / "out.zip"), {"1.0", "2.0"})

    assert {name for name in _names(str(tmp_path / "out.zip")) if name.startswith("mod_versions/")} == {
        "mod_versions/1.0.zip",
        "mod_versions/2.0.zip",
    }


def test_selection_drops_unchecked_versions(mod_dir):
    apply_version_selection(mod_dir, VersionSelection(frozenset({"1.0"})), "3.0")

    assert [version["name"] for version in list_local_versions(mod_dir)] == ["1.0"]
    with open(os.path.join(mod_dir, "data.txt"), encoding="utf-8") as handle:
        assert handle.read() == "three"


def test_selection_without_versions_removes_the_folder(mod_dir):
    apply_version_selection(mod_dir, VersionSelection(frozenset()), "3.0")

    assert not os.path.exists(os.path.join(mod_dir, "mod_versions"))


def test_another_version_can_become_active(mod_dir):
    apply_version_selection(mod_dir, VersionSelection(frozenset({"1.0", "2.0"}), True, "2.0"), "3.0")

    with open(os.path.join(mod_dir, "data.txt"), encoding="utf-8") as handle:
        assert handle.read() == "two"
    assert {version["name"] for version in list_local_versions(mod_dir)} == {"1.0", "3.0"}
    with open(os.path.join(mod_dir, "mod_config.json"), encoding="utf-8") as handle:
        config = json.load(handle)
    assert (config["id"], config["version"]) == ("local_pack", "2.0")


def test_previous_active_version_can_be_left_out(mod_dir):
    apply_version_selection(mod_dir, VersionSelection(frozenset({"1.0"}), False, "1.0"), "3.0")

    with open(os.path.join(mod_dir, "data.txt"), encoding="utf-8") as handle:
        assert handle.read() == "one"
    assert not os.path.exists(os.path.join(mod_dir, "mod_versions"))


def test_the_active_files_cannot_be_both_left_out_and_active(mod_dir):
    with pytest.raises(ValueError):
        apply_version_selection(mod_dir, VersionSelection(frozenset({"1.0"}), False, None), "3.0")


def test_resolve_skips_the_question_without_versions(tmp_path):
    folder = str(tmp_path / "plain")
    _write_mod(folder, "1.0", "x")

    assert resolve_package_versions(folder, lambda *_: pytest.fail("asked")) is True


def test_resolve_reports_a_cancel_and_leaves_the_package_alone(mod_dir):
    assert resolve_package_versions(mod_dir, lambda *_: None) is False

    assert {version["name"] for version in list_local_versions(mod_dir)} == {"1.0", "2.0"}


def test_resolve_describes_the_package_to_the_chooser(mod_dir):
    seen = []

    def choose(name, label, versions):
        seen.append((name, label, [version["name"] for version in versions]))
        return VersionSelection(frozenset({"1.0", "2.0"}))

    assert resolve_package_versions(mod_dir, choose) is True
    assert seen == [("Pack", "3.0", ["1.0", "2.0"])]


def test_settings_combos_are_localized_in_the_order_of_the_modes():
    from config.config import COMBO_LOCALIZATIONS
    from utils.mod.version_package import VERSION_MODES

    for combo in ("mod_versions_export_mode_combo", "mod_versions_import_mode_combo"):
        assert COMBO_LOCALIZATIONS[combo] == [f"mod_versions.mode_{mode}" for mode in VERSION_MODES]
