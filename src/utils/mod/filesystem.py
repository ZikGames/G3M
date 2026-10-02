"""Safe traversal helpers for operation payloads."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


class DirectoryTraversalError(ValueError):
    """A directory cannot be followed safely."""


def iter_directory_tree(root: str | Path) -> Iterator[tuple[Path, str, bool]]:
    """Yield a directory tree while dereferencing links and rejecting cycles.

    Links are read-only input here: callers copy their resolved contents rather
    than recreating a link in the destination.  That permits portable payloads
    without allowing a recursive link to make traversal unbounded.
    """
    source = Path(root)
    if not source.is_dir():
        raise DirectoryTraversalError("path must be an existing directory")

    def walk(directory: Path, prefix: str, ancestors: frozenset[tuple[int, int]]):
        try:
            metadata = directory.stat()
            identity = (metadata.st_dev, metadata.st_ino)
        except OSError as error:
            raise DirectoryTraversalError(f"cannot inspect directory: {directory}") from error
        if identity in ancestors:
            raise DirectoryTraversalError(f"directory link creates a cycle: {directory}")
        next_ancestors = ancestors | {identity}
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            raise DirectoryTraversalError(f"cannot read directory: {directory}") from error
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                yield child, relative, True
                yield from walk(child, relative, next_ancestors)
            elif child.is_file():
                yield child, relative, False
            else:
                raise DirectoryTraversalError(f"unsupported filesystem entry: {child}")

    yield from walk(source, "", frozenset())
