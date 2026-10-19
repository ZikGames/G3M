"""Reinstalling a mod must reuse the folder that already holds its id."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui.dialogs.manual_install.workers import write_import
from utils.file_utils import staged_mod_dir

CONFIG = {
    "config_version": "2.0.0",
    "id": "gb_mod_1",
    "name": "Same Mod",
    "version": "2.0.0",
    "authors": ["a"],
    "game": "deltarune",
    "files": [],
}


def _installed(mods: Path, folder: str) -> Path:
    path = mods / folder
    (path / "mod_versions").mkdir(parents=True)
    (path / "mod_versions" / "1.0.0.zip").write_bytes(b"snapshot")
    (path / "old.txt").write_text("old")
    (path / "mod_config.json").write_text(json.dumps({"id": "gb_mod_1", "name": "Same Mod"}))
    return path


def test_staged_mod_dir_replaces_the_installed_folder_but_keeps_snapshots(tmp_path):
    existing = _installed(tmp_path, "Renamed Folder")

    with staged_mod_dir(str(tmp_path), "Same Mod", "gb_mod_1") as staged:
        (Path(staged.path) / "new.txt").write_text("new")

    assert Path(staged.final) == existing
    assert sorted(path.name for path in existing.iterdir()) == ["mod_versions", "new.txt"]
    assert [path.name for path in tmp_path.iterdir()] == ["Renamed Folder"]


def test_staged_mod_dir_keeps_the_installed_mod_when_the_build_fails(tmp_path):
    existing = _installed(tmp_path, "Renamed Folder")

    with pytest.raises(RuntimeError), staged_mod_dir(str(tmp_path), "Same Mod", "gb_mod_1"):
        raise RuntimeError

    assert (existing / "old.txt").read_text() == "old"
    assert [path.name for path in tmp_path.iterdir()] == ["Renamed Folder"]


def test_staged_mod_dir_creates_a_new_folder_for_an_unknown_id(tmp_path):
    with staged_mod_dir(str(tmp_path), "Fresh Mod", "gb_mod_2") as staged:
        (Path(staged.path) / "a.txt").write_text("a")

    assert Path(staged.final) == tmp_path / "Fresh Mod"
    assert [path.name for path in tmp_path.iterdir()] == ["Fresh Mod"]


def test_manual_import_replaces_the_installed_mod_instead_of_duplicating_it(tmp_path):
    existing = _installed(tmp_path, "Renamed Folder")

    result = write_import([], dict(CONFIG), tmp_path)

    assert result == existing
    assert [path.name for path in tmp_path.iterdir()] == ["Renamed Folder"]
    assert json.loads((existing / "mod_config.json").read_text())["version"] == "2.0.0"
    assert (existing / "mod_versions" / "1.0.0.zip").exists()
    assert not (existing / "old.txt").exists()
