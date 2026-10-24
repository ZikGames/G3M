"""Exporting a mod by dragging it out: instant preview, export only after the mouse is released."""

import os
from pathlib import Path

import pytest

import presentation.drag_drop as drag_drop
from presentation.drag_drop import LazyFileExportMimeData, file_drag_pixmap


def _exporter(calls):
    def export(path):
        calls.append(path)
        with open(path, "wb") as handle:
            handle.write(b"zip data")
        return True

    return export


@pytest.fixture
def calls():
    return []


@pytest.fixture
def mime(qapp, calls):
    data = LazyFileExportMimeData(_exporter(calls), "My Mod.zip")
    yield data
    data.cleanup()


def test_while_the_button_is_held_targets_get_an_empty_placeholder(mime, calls, monkeypatch):
    monkeypatch.setattr(drag_drop, "left_button_is_down", lambda: True)

    path = mime.urls()[0].toLocalFile()

    assert os.path.basename(path) == "My Mod.zip"
    assert os.path.getsize(path) == 0
    assert calls == []


def test_the_export_runs_on_the_first_request_after_the_release(mime, calls, monkeypatch):
    monkeypatch.setattr(drag_drop, "left_button_is_down", lambda: True)
    hover_path = mime.urls()[0].toLocalFile()
    monkeypatch.setattr(drag_drop, "left_button_is_down", lambda: False)

    dropped_path = mime.urls()[0].toLocalFile()
    mime.urls()

    assert dropped_path == hover_path
    assert Path(dropped_path).read_bytes() == b"zip data"
    assert [os.path.normpath(call) for call in calls] == [os.path.normpath(hover_path)]


def test_a_failed_export_hands_out_nothing(qapp, monkeypatch):
    monkeypatch.setattr(drag_drop, "left_button_is_down", lambda: False)
    data = LazyFileExportMimeData(lambda _path: False, "My Mod.zip")

    assert data.urls() == []
    assert not data.has_materialized_export()


def test_cleanup_removes_the_temporary_folder(mime, monkeypatch):
    monkeypatch.setattr(drag_drop, "left_button_is_down", lambda: False)
    path = mime.urls()[0].toLocalFile()

    mime.cleanup()

    assert not os.path.exists(os.path.dirname(path))


def test_the_preview_shows_the_icon_and_name_of_the_file_at_once(qapp):
    pixmap = file_drag_pixmap("A very long mod name that has to be shortened to fit next to the cursor.zip")

    assert not pixmap.isNull()
    assert pixmap.deviceIndependentSize().width() <= 240
