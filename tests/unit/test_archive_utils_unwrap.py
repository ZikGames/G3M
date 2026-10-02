"""Unit tests for test archive utils unwrap."""

import zipfile

import pytest

from utils.archive_utils import (
    extract_archive_content_root,
    get_file_extension_from_url,
    unwrap_single_directory_chain,
)
from utils.mod.archive import ARCHIVE_SUFFIXES


@pytest.mark.parametrize("extension", ARCHIVE_SUFFIXES)
def test_download_extension_preserves_the_registered_archive_format(extension):
    url = f"https://example.com/mod{extension.upper()}?download=1"

    assert get_file_extension_from_url(url) == extension


@pytest.mark.parametrize(
    ("mime_type", "extension"),
    [("application/x-tar", ".tar"), ("application/gzip", ".tar.gz"), ("application/x-bzip2", ".tar.bz2"), ("application/x-xz", ".tar.xz"), ("application/zip; charset=binary", ".zip")],
)
def test_download_extension_uses_supported_mime_formats(mime_type, extension):
    assert get_file_extension_from_url("https://example.com/download", mime_type) == extension


def test_unwrap_single_directory_chain_descends_until_content_branches(tmp_path):
    """Checks that unwrapping single directory chain descends until content branches."""
    deepest = tmp_path / "outer" / "inner" / "payload"
    deepest.mkdir(parents=True)
    (deepest / "mod_config.json").write_text("{}", encoding="utf-8")
    (deepest / "data.win").write_text("patched", encoding="utf-8")

    resolved = unwrap_single_directory_chain(str(tmp_path))

    assert resolved == str(deepest)


def test_unwrap_single_directory_chain_stops_when_single_entry_is_not_directory(tmp_path):
    """Checks that unwrapping single directory chain stops when single entry is not directory."""
    marker = tmp_path / "archive.zip"
    marker.write_text("payload", encoding="utf-8")

    resolved = unwrap_single_directory_chain(str(tmp_path))

    assert resolved == str(tmp_path)


def test_extract_archive_content_root_stops_between_members_when_cancelled(tmp_path):
    archive = tmp_path / "mod.zip"
    target = tmp_path / "out"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("first.txt", "first")
        handle.writestr("second.txt", "second")
    checks = 0

    def is_cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    extract_archive_content_root(str(archive), str(target), is_cancelled=is_cancelled)

    assert checks >= 2
    assert not (target / "first.txt").exists()
    assert not (target / "second.txt").exists()


def test_extract_archive_content_root_rejects_links(tmp_path):
    archive = tmp_path / "linked.zip"
    target = tmp_path / "out"
    link = zipfile.ZipInfo("link")
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(link, "outside")

    with pytest.raises(ValueError, match="is a link"):
        extract_archive_content_root(str(archive), str(target))

    assert not (target / "link").exists()
