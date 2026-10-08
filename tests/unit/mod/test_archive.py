"""Contract tests for the operation archive registry."""

from __future__ import annotations

import lzma
import zipfile

import pytest

from utils.mod.archive import (
    ArchiveValidationError,
    archive_format,
    archive_write_supported,
    list_archive_members,
    materialize_archive,
    rebuild_archive,
    split_archive_virtual_path,
)


def test_archive_named_directory_can_contain_a_real_archive(tmp_path):
    folder = tmp_path / "pack.zip"
    folder.mkdir()
    archive = folder / "mod.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("README.txt", "Guide")
    assert archive_format(folder) is None
    assert split_archive_virtual_path(folder / "file.txt") is None
    virtual = split_archive_virtual_path(archive / "README.txt")
    assert virtual is not None
    assert virtual.archive == archive
    assert virtual.member == "README.txt"


@pytest.mark.parametrize(
    "suffix",
    [
        ".zip",
        ".7z",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.bz2",
        ".tbz2",
        ".tar.xz",
        ".txz",
        ".tar.lzma",
    ],
)
def test_writable_archives_round_trip_a_directory_tree(tmp_path, suffix):
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "nested" / "text.txt").write_text("text", encoding="utf-8")
    archive = tmp_path / f"mod{suffix}"

    rebuild_archive(archive, source)

    if suffix == ".tar.lzma":
        assert not archive.read_bytes().startswith(b"\xfd7zXZ\x00")
    members = list_archive_members(archive)
    materialized = tmp_path / "materialized"
    materialize_archive(archive, materialized)

    assert {member.name.rstrip("/") for member in members} >= {"nested", "nested/text.txt", "empty"}
    assert (materialized / "nested" / "text.txt").read_text(encoding="utf-8") == "text"
    assert (materialized / "empty").is_dir()


def test_lzma_round_trip_is_one_file_stream(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"payload")
    archive = tmp_path / "payload.lzma"

    rebuild_archive(archive, source)

    assert not archive.read_bytes().startswith(b"\xfd7zXZ\x00")
    materialized = tmp_path / "materialized"
    materialize_archive(archive, materialized)

    assert (materialized / "payload").read_bytes() == b"payload"
    assert archive_write_supported(archive)


def test_lzma_extraction_enforces_the_uncompressed_size_limit(tmp_path, monkeypatch):
    archive = tmp_path / "payload.lzma"
    with lzma.open(archive, "wb") as writer:
        writer.write(b"x" * 1024)
    monkeypatch.setattr("utils.mod.archive.ARCHIVE_MAX_MEMBER_BYTES", 128)

    with pytest.raises(ArchiveValidationError, match="exceeds the size limit"):
        materialize_archive(archive, tmp_path / "materialized")


def test_lzma_extraction_enforces_the_compression_ratio_limit(tmp_path, monkeypatch):
    archive = tmp_path / "payload.lzma"
    with lzma.open(archive, "wb") as writer:
        writer.write(b"x" * 1024)
    monkeypatch.setattr("utils.mod.archive.ARCHIVE_MAX_MEMBER_BYTES", 10_000)
    monkeypatch.setattr("utils.mod.archive.ARCHIVE_MAX_COMPRESSION_RATIO", 1)

    with pytest.raises(ArchiveValidationError, match="compression ratio limit"):
        materialize_archive(archive, tmp_path / "materialized")


def test_virtual_paths_use_longest_extension_and_lzma_has_no_members():
    virtual = split_archive_virtual_path("C:/mods/example.tar.gz/assets/icon.png")

    assert virtual is not None
    assert virtual.archive.as_posix().endswith("example.tar.gz")
    assert virtual.member == "assets/icon.png"
    assert virtual.directory is False
    archive_root = split_archive_virtual_path("C:/mods/example.zip/")
    assert archive_root is not None
    assert archive_root.member == ""
    with pytest.raises(ArchiveValidationError, match="do not have virtual members"):
        split_archive_virtual_path("C:/mods/example.lzma/member")
    assert not archive_write_supported("C:/mods/example.rar")


def test_archive_member_validation_rejects_case_collisions(tmp_path):
    archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("File.txt", "one")
        writer.writestr("file.txt", "two")

    with pytest.raises(ArchiveValidationError, match="duplicate"):
        list_archive_members(archive)


@pytest.mark.parametrize(
    "member_name",
    ["D:/outside.txt", "folder/D:/outside.txt", "C:outside.txt", "file.txt:stream", "NUL", "folder/CON.txt", "folder/file.", "folder/file "],
)
def test_archive_rejects_windows_drive_device_and_aliased_member_paths(tmp_path, member_name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr(member_name, "payload")

    with pytest.raises(ArchiveValidationError, match="unsafe"):
        materialize_archive(archive, tmp_path / "materialized")


@pytest.mark.parametrize("suffix", [".zip", ".tar", ".tar.gz", ".7z"])
def test_archive_rebuild_dereferences_a_source_link(tmp_path, suffix):
    source = tmp_path / "source"
    source.mkdir()
    payload = tmp_path / "payload.txt"
    payload.write_text("linked", encoding="utf-8")
    try:
        (source / "linked.txt").symlink_to(payload)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    archive = tmp_path / f"mod{suffix}"

    rebuild_archive(archive, source)

    materialized = tmp_path / "materialized"
    materialize_archive(archive, materialized)
    assert (materialized / "linked.txt").read_text(encoding="utf-8") == "linked"
