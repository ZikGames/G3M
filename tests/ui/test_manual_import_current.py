"""Regression tests for the current manual-import flow."""

from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QWidget

from ui.dialogs.manual_install.dialog import ManualModInstallDialog
from utils.mod.config import load_mod_config


def _parent(tmp_path):
    parent = QWidget()
    parent.app_state = SimpleNamespace(local_config={}, mods_dir=str(tmp_path / "mods"))
    parent.mod_service = object()
    return parent


def test_manual_import_copies_sources_without_guessing_operations(qapp, tmp_path):
    prepared = tmp_path / "prepared"
    (prepared / "assets").mkdir(parents=True)
    (prepared / "README.md").write_text("Guide", encoding="utf-8")
    (prepared / "assets" / "patch.xdelta").write_text("patch", encoding="utf-8")
    parent = _parent(tmp_path)
    dialog = ManualModInstallDialog(parent, str(prepared))

    assert dialog.sources.topLevelItemCount() == 2
    assert not hasattr(dialog, "data_file_selections")
    assert not hasattr(dialog, "additional_patches_mappings")
    config, folder = dialog._create_mod_from_files()
    saved = load_mod_config(folder / "mod_config.json")

    assert (folder / "files" / "README.md").read_text(encoding="utf-8") == "Guide"
    assert (folder / "files" / "assets" / "patch.xdelta").read_text(encoding="utf-8") == "patch"
    assert config == saved
    assert saved["files"] == [{"source": "${mod_path}/files/README.md", "type": "info"}]


def test_manual_import_keeps_the_downloads_original_profile(qapp, tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "README.md").write_text("Guide", encoding="utf-8")
    parent = _parent(tmp_path)
    original_profile = tmp_path / "original_profile"
    dialog = ManualModInstallDialog(parent, str(prepared), target_mods_dir=str(original_profile))

    _config, folder = dialog._create_mod_from_files()

    assert folder.parent == original_profile
    assert not (tmp_path / "mods").exists()


def test_manual_import_passes_the_new_mod_to_the_current_editor(qapp, tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "patch.xdelta").write_text("patch", encoding="utf-8")
    parent = _parent(tmp_path)
    dialog = ManualModInstallDialog(parent, str(prepared))
    opened = []
    monkeypatch.setattr(
        dialog,
        "_open_editor",
        lambda config, folder: opened.append((config, folder)) or True,
    )

    dialog._on_finish()

    assert dialog.result() == dialog.DialogCode.Accepted
    assert len(opened) == 1
    assert opened[0][0]["files"] == []
    assert (opened[0][1] / "files" / "patch.xdelta").is_file()


def test_manual_import_cleans_up_when_editor_is_cancelled(qapp, tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "patch.xdelta").write_text("patch", encoding="utf-8")
    parent = _parent(tmp_path)
    dialog = ManualModInstallDialog(parent, str(prepared))
    created = []

    def cancel_editor(_config, folder):
        created.append(folder)
        return False

    monkeypatch.setattr(dialog, "_open_editor", cancel_editor)

    dialog._on_finish()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert created and not created[0].exists()


def test_manual_import_ignores_external_file_symlinks(tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    try:
        (prepared / "linked.txt").symlink_to(external)
    except OSError:
        return

    dialog = ManualModInstallDialog.__new__(ManualModInstallDialog)
    dialog.prepared_files_path = str(prepared)

    assert dialog._scan_files() == []
