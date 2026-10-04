"""Canonical SHA-256 helpers for operation files and directory trees."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from pathlib import Path

from utils.mod.filesystem import DirectoryTraversalError, iter_directory_tree

HASH_PREFIX = "sha256:"
HASH_CHUNK_SIZE = 1024 * 1024


class HashValidationError(ValueError):
    """Raised when a path cannot safely participate in integrity checks."""


def sha256_path(path: str | Path, *, cancelled: Callable[[], bool] = lambda: False) -> str:
    """Return the stable operation SHA-256 identifier for one file or directory."""
    if cancelled():
        raise InterruptedError
    target = Path(path)
    if target.is_file():
        return f"{HASH_PREFIX}{_hash_file(target, cancelled).hexdigest()}"
    if target.is_dir():
        return f"{HASH_PREFIX}{_hash_directory(target, cancelled).hexdigest()}"
    raise HashValidationError("path must be an existing regular file or directory")


def _hash_file(path: Path, cancelled: Callable[[], bool]) -> hashlib._Hash:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
            if cancelled():
                raise InterruptedError
            digest.update(block)
    return digest


def _hash_directory(root: Path, cancelled: Callable[[], bool]) -> hashlib._Hash:
    digest = hashlib.sha256()
    try:
        entries = []
        for path, relative, directory in iter_directory_tree(root):
            if cancelled():
                raise InterruptedError
            entries.append((unicodedata.normalize("NFC", relative), path, directory))
    except DirectoryTraversalError as error:
        raise HashValidationError(str(error)) from error
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    previous: str | None = None
    for relative, path, directory in entries:
        if cancelled():
            raise InterruptedError
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
                if cancelled():
                    raise InterruptedError
                digest.update(block)
    return digest


def _write_field(digest: hashlib._Hash, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)
