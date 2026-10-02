"""Canonical SHA-256 helpers for operation files and directory trees."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from utils.mod.filesystem import DirectoryTraversalError, iter_directory_tree

HASH_PREFIX = "sha256:"
HASH_CHUNK_SIZE = 1024 * 1024


class HashValidationError(ValueError):
    """Raised when a path cannot safely participate in integrity checks."""


def sha256_path(path: str | Path) -> str:
    """Return the stable operation SHA-256 identifier for one file or directory."""
    target = Path(path)
    if target.is_file():
        return f"{HASH_PREFIX}{_hash_file(target).hexdigest()}"
    if target.is_dir():
        return f"{HASH_PREFIX}{_hash_directory(target).hexdigest()}"
    raise HashValidationError("path must be an existing regular file or directory")


def _hash_file(path: Path) -> hashlib._Hash:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
            digest.update(block)
    return digest


def _hash_directory(root: Path) -> hashlib._Hash:
    digest = hashlib.sha256()
    try:
        entries = [
            (unicodedata.normalize("NFC", relative), path, directory)
            for path, relative, directory in iter_directory_tree(root)
        ]
    except DirectoryTraversalError as error:
        raise HashValidationError(str(error)) from error
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    previous: str | None = None
    for relative, path, directory in entries:
        if relative == previous:
            raise HashValidationError("directory contains equivalent normalized paths")
        previous = relative
        _write_field(digest, b"D" if directory else b"F")
        _write_field(digest, relative.encode("utf-8"))
        if directory:
            _write_field(digest, b"")
            continue
        size = path.stat().st_size
        _write_field(digest, size.to_bytes(8, "big"))
        with path.open("rb") as source:
            for block in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
                digest.update(block)
    return digest


def _write_field(digest: hashlib._Hash, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)
