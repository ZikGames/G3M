"""Only saved versions with a supported mod config are offered or installed."""

import json
import os
import zipfile

import pytest

from utils.mod.version_package import (
    VersionSelection,
    resolve_package_versions,
    split_valid_versions,
)
from utils.mod.version_utils import create_version_zip, list_local_versions

LEGACY_CONFIG = {"id": "local_pack", "name": "Pack", "version": "1", "game": "deltarune", "files": {}}


def _write_mod(folder, version):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "mod_config.json"), "w", encoding="utf-8") as handle:
        json.dump({**LEGACY_CONFIG, "version": version}, handle)


def _zip(path, member, content):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, content)
    return str(path)


@pytest.fixture
def mod_dir(tmp_path):
    folder = str(tmp_path / "pack")
    _write_mod(folder, "3.0")
    for name in ("1.0", "2.0"):
        staged = str(tmp_path / f"stage_{name}")
        _write_mod(staged, name)
        create_version_zip(staged, folder, name)
    return folder


def test_a_version_counts_when_its_config_migrates_to_the_current_format(tmp_path):
    versions = [
        {"name": "legacy", "path": _zip(tmp_path / "a.zip", "Pack/mod_config.json", json.dumps(LEGACY_CONFIG))},
        {"name": "broken", "path": _zip(tmp_path / "b.zip", "Pack/mod_config.json", "{not json")},
        {"name": "bare", "path": _zip(tmp_path / "c.zip", "Pack/readme.txt", "no config")},
        {"name": "missing", "path": str(tmp_path / "missing.zip")},
    ]

    valid, invalid = split_valid_versions(versions)

    assert [version["name"] for version in valid] == ["legacy"]
    assert [version["name"] for version in invalid] == ["broken", "bare", "missing"]


def test_resolve_offers_only_usable_versions_and_drops_the_rest(mod_dir):
    _zip(os.path.join(mod_dir, "mod_versions", "0.5.zip"), "notes.txt", "no config in here")
    offered = []

    def choose(_name, _label, versions):
        offered.extend(version["name"] for version in versions)
        return VersionSelection(frozenset(offered))

    assert resolve_package_versions(mod_dir, choose) is True

    assert offered == ["1.0", "2.0"]
    assert {version["name"] for version in list_local_versions(mod_dir)} == {"1.0", "2.0"}


def test_resolve_ignores_packages_whose_own_config_is_unusable(mod_dir):
    with open(os.path.join(mod_dir, "mod_config.json"), "w", encoding="utf-8") as handle:
        handle.write("{broken")

    assert resolve_package_versions(mod_dir, lambda *_: pytest.fail("asked")) is True
    assert len(list_local_versions(mod_dir)) == 2


def test_resolve_with_only_unusable_versions_removes_them_without_asking(tmp_path):
    folder = str(tmp_path / "pack")
    _write_mod(folder, "1.0")
    os.makedirs(os.path.join(folder, "mod_versions"))
    _zip(os.path.join(folder, "mod_versions", "old.zip"), "notes.txt", "x")

    assert resolve_package_versions(folder, lambda *_: pytest.fail("asked")) is True
    assert not os.path.exists(os.path.join(folder, "mod_versions"))
