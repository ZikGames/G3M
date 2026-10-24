"""Icons placed into a mod while it is edited: Browse replaces at once, Save finishes, Cancel undoes."""

from pathlib import Path

import pytest

from utils.mod.icon_files import IconFiles


@pytest.fixture
def mod(tmp_path):
    root = tmp_path / "mod"
    root.mkdir()
    (root / "icon.png").write_bytes(b"old icon")
    return root


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


def _files(mod, referenced=lambda _path: False):
    return IconFiles(str(mod), str(mod / "icon.png"), referenced)


def _picked(outside, name, data=b"new icon"):
    path = outside / name
    path.write_bytes(data)
    return str(path)


def test_browse_replaces_the_icon_the_mod_had(mod, outside):
    files = _files(mod)

    name = files.adopt(_picked(outside, "logo.png"), str(mod / "icon.png"))

    assert name == "logo.png"
    assert not (mod / "icon.png").exists()
    assert (mod / "logo.png").read_bytes() == b"new icon"


def test_the_picked_file_outside_the_mod_is_never_touched(mod, outside):
    source = _picked(outside, "logo.png")

    _files(mod).adopt(source, str(mod / "icon.png"))

    assert Path(source).read_bytes() == b"new icon"


def test_cancel_after_browse_restores_the_old_icon(mod, outside):
    files = _files(mod)
    files.adopt(_picked(outside, "logo.png"), str(mod / "icon.png"))

    files.rollback()

    assert (mod / "icon.png").read_bytes() == b"old icon"
    assert not (mod / "logo.png").exists()


def test_an_icon_outside_the_mod_is_not_replaced(mod, outside):
    files = _files(mod)

    files.adopt(_picked(outside, "logo.png"), str(outside / "elsewhere.png"))

    assert (mod / "icon.png").read_bytes() == b"old icon"
    assert (mod / "logo.png").is_file()


def test_an_icon_that_an_operation_uses_stays(mod, outside):
    files = _files(mod, referenced=lambda _path: True)

    files.adopt(_picked(outside, "logo.png"), str(mod / "icon.png"))

    assert (mod / "icon.png").read_bytes() == b"old icon"


def test_a_picture_with_the_name_of_the_replaced_icon_takes_its_place(mod, outside):
    files = _files(mod)

    name = files.adopt(_picked(outside, "icon.png"), str(mod / "icon.png"))

    assert name == "icon.png"
    assert (mod / "icon.png").read_bytes() == b"new icon"
    files.rollback()
    assert (mod / "icon.png").read_bytes() == b"old icon"


def test_a_taken_name_gets_a_numbered_copy(mod, outside):
    (mod / "logo.png").write_bytes(b"another file")
    files = _files(mod)

    name = files.adopt(_picked(outside, "logo.png"), str(outside / "elsewhere.png"))

    assert name == "logo_1.png"
    assert (mod / "logo.png").read_bytes() == b"another file"


def test_browsing_again_swaps_the_previous_pick_and_keeps_the_first_backup(mod, outside):
    files = _files(mod)
    files.adopt(_picked(outside, "first.png"), str(mod / "icon.png"))

    files.adopt(_picked(outside, "second.png", b"second"), str(mod / "first.png"))

    assert not (mod / "first.png").exists()
    assert (mod / "second.png").is_file()
    files.rollback()
    assert (mod / "icon.png").read_bytes() == b"old icon"
    assert not (mod / "second.png").exists()


def test_picking_the_file_that_was_just_copied_keeps_it(mod, outside):
    files = _files(mod)
    files.adopt(_picked(outside, "logo.png"), str(mod / "icon.png"))

    name = files.adopt(str(mod / "logo.png"), str(mod / "logo.png"))

    assert name == "logo.png"
    assert (mod / "logo.png").is_file()


def test_commit_drops_a_picked_icon_the_config_no_longer_uses(mod, outside):
    files = _files(mod)
    files.adopt(_picked(outside, "logo.png"), str(mod / "icon.png"))

    files.commit(None)

    assert not (mod / "logo.png").exists()


def test_commit_keeps_the_picked_icon_and_forgets_the_backup(mod, outside):
    files = _files(mod)
    files.adopt(_picked(outside, "logo.png"), str(mod / "icon.png"))

    files.commit("logo.png")
    files.rollback()

    assert (mod / "logo.png").is_file()
    assert not (mod / "icon.png").exists()


def test_save_copies_a_typed_path_and_removes_the_old_icon_afterwards(mod, outside):
    files = _files(mod)

    name = files.place(_picked(outside, "logo.png"))

    assert name == "logo.png"
    assert (mod / "icon.png").exists()
    files.commit(name)
    assert not (mod / "icon.png").exists()
    assert (mod / "logo.png").read_bytes() == b"new icon"


def test_save_overwrites_the_old_icon_only_when_the_config_is_written(mod, outside):
    files = _files(mod)

    name = files.place(_picked(outside, "icon.png"))

    assert name == "icon.png"
    assert (mod / "icon.png").read_bytes() == b"old icon"
    files.commit(name)
    assert (mod / "icon.png").read_bytes() == b"new icon"
    assert not (mod / "icon.png.new").exists()


def test_cancel_after_a_typed_replacement_leaves_the_mod_as_it_was(mod, outside):
    files = _files(mod)
    files.place(_picked(outside, "icon.png"))

    files.rollback()

    assert (mod / "icon.png").read_bytes() == b"old icon"
    assert not (mod / "icon.png.new").exists()


def test_a_typed_path_with_another_files_name_gets_a_numbered_copy(mod, outside):
    (mod / "logo.png").write_bytes(b"another file")
    files = _files(mod)

    name = files.place(_picked(outside, "logo.png"))

    assert name == "logo_1.png"
    assert (mod / "logo.png").read_bytes() == b"another file"


def test_a_name_already_inside_the_mod_is_used_as_it_is(mod):
    (mod / "other.png").write_bytes(b"other")
    files = _files(mod)

    name = files.place("other.png")
    files.commit(name)

    assert name == "other.png"
    assert not (mod / "icon.png").exists()


def test_a_typed_path_that_does_not_exist_places_nothing(mod):
    assert _files(mod).place("gone.png") is None
    assert (mod / "icon.png").exists()


def test_a_relative_name_is_looked_up_in_the_mod_not_the_working_directory(mod, tmp_path, monkeypatch):
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    (elsewhere / "other.png").write_bytes(b"wrong file")
    monkeypatch.chdir(elsewhere)
    (mod / "other.png").write_bytes(b"right file")

    assert _files(mod).place("other.png") == "other.png"
    assert (mod / "other.png").read_bytes() == b"right file"


def test_a_failing_commit_does_not_raise(mod, outside, monkeypatch):
    files = _files(mod)
    name = files.place(_picked(outside, "icon.png"))
    monkeypatch.setattr("os.replace", lambda *_args: (_ for _ in ()).throw(PermissionError("locked")))

    files.commit(name)

    assert (mod / "icon.png").read_bytes() == b"old icon"
