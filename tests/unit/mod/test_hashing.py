"""Tests for canonical operation integrity hashes."""

from __future__ import annotations

import pytest

from utils.mod.hashing import sha256_path


def test_file_hash_is_standard_sha256(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"abc")

    digest = sha256_path(payload)

    assert digest == "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_path(payload) == digest


def test_directory_hash_is_stable_and_includes_empty_directories(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "empty").mkdir()
        (root / "nested" / "payload.txt").write_text("same", encoding="utf-8")

    digest = sha256_path(first)

    assert digest == sha256_path(second)
    (second / "empty").rmdir()
    assert digest != sha256_path(second)


def test_directory_hash_dereferences_links_and_rejects_cycles(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    payload = tmp_path / "payload.txt"
    payload.write_text("linked", encoding="utf-8")
    try:
        (source / "linked.txt").symlink_to(payload)
        (source / "cycle").symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    from utils.mod.hashing import HashValidationError

    with pytest.raises(HashValidationError, match="cycle"):
        sha256_path(source)
    (source / "cycle").unlink()
    assert sha256_path(source) == sha256_path(source)
