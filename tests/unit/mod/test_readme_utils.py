"""Tests for operation listed and unlisted information files."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from utils.mod.readme_utils import (
    find_mod_readme_files,
    find_mod_unlisted_readme_files,
    is_markdown_file,
    read_mod_readme,
)


def test_missing_archived_pdf_displays_error(qapp, tmp_path):
    from ui.dialogs.mod.readme_dialog import _ReadmeTab

    tab = _ReadmeTab(str(tmp_path / "missing.zip" / "readme.pdf"))
    tab.load_content()

    assert tab.pdf_viewer.isHidden()
    assert not tab.pdf_error_label.isHidden()
    assert tab.pdf_error_label.text()
    tab.dispose()


def test_readme_dispose_cleans_extracted_files_even_before_loading(qapp, tmp_path):
    from ui.dialogs.mod.readme_dialog import _ReadmeTab

    tab = _ReadmeTab(str(tmp_path / "README.txt"))
    tab._temporary_directory = TemporaryDirectory()
    extracted = Path(tab._temporary_directory.name)
    (extracted / "README.txt").write_text("readme", encoding="utf-8")
    tab.dispose()

    assert not extracted.exists()
    assert tab._temporary_directory is None


def _write_config(
    folder: Path, files: list[object], *, placeholders: dict[str, str] | None = None
) -> None:
    config: dict[str, object] = {
        "config_version": "2.0.0",
        "id": "readme-test",
        "name": "Readme Test",
        "version": "1.0.0",
        "authors": [],
        "game": "undertale",
        "files": files,
    }
    if placeholders:
        config["placeholders"] = placeholders
    (folder / "mod_config.json").write_text(json.dumps(config), encoding="utf-8")


def test_operation_info_files_keep_config_order_before_unlisted_files(temp_dir):
    folder = Path(temp_dir)
    (folder / "A.txt").write_text("A", encoding="utf-8")
    (folder / "B.txt").write_text("B", encoding="utf-8")
    (folder / "docs").mkdir()
    (folder / "docs" / "C.md").write_text("C", encoding="utf-8")
    _write_config(
        folder,
        [
            {"source": "${mod_path}/B.txt", "type": "info"},
            {"Guides": [{"source": "${mod_path}/docs/C.md", "type": "info"}]},
        ],
    )

    listed = find_mod_readme_files(temp_dir, include_unlisted=False)
    all_files = find_mod_readme_files(temp_dir)

    assert [Path(path).relative_to(folder).as_posix() for path in listed] == ["B.txt", "docs/C.md"]
    assert [Path(path).relative_to(folder).as_posix() for path in all_files] == [
        "B.txt",
        "docs/C.md",
        "A.txt",
    ]


def test_unlisted_info_files_are_physical_and_do_not_scan_archives(temp_dir):
    folder = Path(temp_dir)
    (folder / "README.md").write_text("top", encoding="utf-8")
    (folder / "nested").mkdir()
    (folder / "nested" / "notes.txt").write_text("nested", encoding="utf-8")
    (folder / "docs.zip").write_bytes(b"not inspected")

    found = find_mod_unlisted_readme_files(temp_dir)

    assert [Path(path).relative_to(folder).as_posix() for path in found] == [
        "nested/notes.txt",
        "README.md",
    ]


def test_listed_info_files_expand_mod_path_aliases(temp_dir):
    folder = Path(temp_dir)
    (folder / "docs").mkdir()
    (folder / "docs" / "Guide.md").write_text("guide", encoding="utf-8")
    _write_config(
        folder,
        [{"source": "${docs}/Guide.md", "type": "info"}],
        placeholders={"docs": "${mod_path}/docs"},
    )

    assert find_mod_readme_files(temp_dir, include_unlisted=False) == [
        str(folder / "docs" / "Guide.md")
    ]


def test_listed_info_files_can_be_archive_members(temp_dir):
    folder = Path(temp_dir)
    archive = folder / "docs.zip"
    with ZipFile(archive, "w") as writer:
        writer.writestr("Guide.md", "guide")
    _write_config(
        folder,
        [{"source": "${mod_path}/docs.zip/Guide.md", "type": "info"}],
    )

    assert find_mod_readme_files(temp_dir, include_unlisted=False) == [
        f"{archive}/Guide.md"
    ]


def test_read_mod_readme_supports_utf8_sig(temp_dir):
    file_path = Path(temp_dir) / "README.txt"
    file_path.write_text("Hello README", encoding="utf-8-sig")

    assert read_mod_readme(str(file_path)) == "Hello README"
    assert is_markdown_file(str(file_path)) is False
    assert is_markdown_file(str(Path(temp_dir) / "README.md")) is True
