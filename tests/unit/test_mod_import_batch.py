import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from controllers.mod.import_export_controller import ModImportExportController


@pytest.mark.parametrize("matching_child", [False, True])
def test_manual_import_batch_flattens_single_enclosing_directory(matching_child):
    captured = {}

    class Presenter:
        def prompt_with_manual_options(self, _parent, **kwargs):
            captured.update(kwargs)

    controller = ModImportExportController(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(pizza_oven_conversion_presenter=Presenter())
    )

    def materialize(_file_path, destination):
        nested = Path(destination) / "enclosing"
        nested.mkdir(parents=True)
        (nested / "mod.xdelta").write_text("patch", encoding="utf-8")
        if matching_child:
            (nested / "enclosing").write_text("payload", encoding="utf-8")
        return str(nested)

    vars(controller)["_materialize_local_import"] = materialize
    controller._show_manual_import_batch(["mod.zip"])

    prepared = captured["prepared_path"]
    prepared_path = Path(prepared) / "0001"
    assert list(prepared_path.rglob("*"))
    if matching_child:
        assert (prepared_path / "enclosing").read_text(encoding="utf-8") == "payload"
    else:
        assert not (prepared_path / "enclosing").exists()
    assert (prepared_path / "mod.xdelta").is_file()
    shutil.rmtree(prepared, ignore_errors=True)


def test_manual_directory_batch_excludes_external_links(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("payload", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (source / "file-link.txt").symlink_to(external / "secret.txt")
        (source / "directory-link").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    staged = tmp_path / "staged"
    staged.mkdir()
    monkeypatch.setattr(
        "controllers.mod.import_export_controller.tempfile.mkdtemp", lambda **_kwargs: str(staged),
    )
    captured = {}

    class Presenter:
        def prompt_with_manual_options(self, _parent, **kwargs):
            captured.update(kwargs)

    controller = ModImportExportController(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(pizza_oven_conversion_presenter=Presenter()),
    )
    controller._show_manual_import_batch([str(source)])

    assert captured["prepared_path"] == str(staged)
    assert (staged / "0001" / "payload.txt").read_text(encoding="utf-8") == "payload"
    assert not (staged / "0001" / "file-link.txt").exists()
    assert not (staged / "0001" / "directory-link").exists()
    assert (external / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_local_directory_import_does_not_copy_its_own_staging_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("payload", encoding="utf-8")
    destination = source / "staged"
    destination.mkdir()
    controller = ModImportExportController(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())

    prepared = controller._materialize_local_import(str(source), str(destination))

    assert prepared == str(destination)
    assert (destination / "payload.txt").read_text(encoding="utf-8") == "payload"
    assert not (destination / "staged").exists()
