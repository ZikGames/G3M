"""Archive extraction utilities."""

import contextlib
import errno
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from urllib.parse import unquote, urlparse

from utils.file_utils import (
    safe_move,
    safe_remove,
    safe_rmtree,
)
from utils.mod.archive import (
    ARCHIVE_SUFFIXES,
    archive_format,
    materialize_archive,
)

logger = logging.getLogger(__name__)
def unwrap_single_directory_chain(root_dir: str) -> str:
    """Descend through nested single-directory layers until content branches."""

    current_dir = os.path.abspath(os.fspath(root_dir))
    visited = {os.path.realpath(current_dir)}
    while True:
        try:
            entries = os.listdir(current_dir)
        except OSError:
            return current_dir
        if len(entries) != 1:
            return current_dir
        next_dir = os.path.join(current_dir, entries[0])
        if not os.path.isdir(next_dir):
            return current_dir
        real_next_dir = os.path.realpath(next_dir)
        if real_next_dir in visited:
            return current_dir
        visited.add(real_next_dir)
        current_dir = next_dir


def _move_tree_safely(src_root: str, dst_root: str) -> None:
    """Safely move directory tree with path traversal protection.

    Args:
        src_root: Source directory.
        dst_root: Destination directory.
    """
    for root, _dirs, files in os.walk(src_root):
        rel_root = os.path.relpath(root, src_root)
        rel_root = "" if rel_root == "." else rel_root
        if os.path.isabs(rel_root):
            continue
        if len(rel_root) >= 2 and rel_root[1] == ":" and rel_root[0].isalpha():
            continue
        dst_dir = os.path.join(dst_root, rel_root) if rel_root else dst_root
        os.makedirs(dst_dir, exist_ok=True)
        for f in files:
            src_path = os.path.join(root, f)
            if os.path.islink(src_path):
                continue
            dst_path = os.path.join(dst_dir, f)
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            try:
                shutil.move(src_path, dst_path)
            except (OSError, shutil.Error):
                try:
                    shutil.copy2(src_path, dst_path)
                except OSError as e:
                    if e.errno != errno.ENOENT:
                        raise


def _cleanup_extracted_archive(target_dir: str, is_game_installation: bool = False):
    if not is_game_installation:
        return
    try:
        single = unwrap_single_directory_chain(target_dir)
        if os.path.normcase(os.path.normpath(single)) != os.path.normcase(
            os.path.normpath(target_dir)
        ):
            for item in os.listdir(single):
                dst = os.path.join(target_dir, item)
                if os.path.exists(dst):
                    (safe_rmtree if os.path.isdir(dst) else safe_remove)(dst)
                safe_move(os.path.join(single, item), dst)
            current = single
            while os.path.normcase(os.path.normpath(current)) != os.path.normcase(
                os.path.normpath(target_dir)
            ):
                parent = os.path.dirname(current)
                with contextlib.suppress(OSError):
                    os.rmdir(current)
                current = parent
    except Exception as e:
        logger.warning(f"Failed to handle nested folder: {e}")
    pattern = re.compile(r"^chapter\d+_(windows|mac)$", re.I)
    for root, dirs, files in os.walk(target_dir, topdown=False):
        del files
        for d in dirs[:]:
            if pattern.match(d) and safe_rmtree(os.path.join(root, d)):
                dirs.remove(d)


class ArchiveExtractor:
    @staticmethod
    def extract(
        archive_path: str,
        target_dir: str,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Archive not found: {archive_path}")
        if not os.path.isfile(archive_path):
            raise ValueError(f"Path is not a file: {archive_path}")
        os.makedirs(target_dir, exist_ok=True)
        try:
            if is_cancelled and is_cancelled():
                return
            if archive_format(archive_path) is None:
                shutil.copy2(archive_path, os.path.join(target_dir, os.path.basename(archive_path)))
                return
            with tempfile.TemporaryDirectory(prefix="g3m-extract-") as temporary:
                materialize_archive(archive_path, temporary)
                if is_cancelled and is_cancelled():
                    return
                _move_tree_safely(temporary, target_dir)
            logger.debug(
                f"ArchiveExtractor: Successfully extracted {archive_path} to {target_dir}"
            )
        except Exception as e:
            error_msg = f"Failed to extract archive {archive_path}: {e}"
            logger.error(error_msg, exc_info=True)
            if isinstance(e, (FileNotFoundError, PermissionError, OSError, ValueError)):
                raise
            raise ValueError(error_msg) from e

    @staticmethod
    def extract_with_options(
        archive_path: str,
        target_dir: str,
        fname: str | None = None,
        is_game_installation: bool = False,
        size_cap_bytes: int | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        os.makedirs(target_dir, exist_ok=True)
        if size_cap_bytes is not None:
            with tempfile.TemporaryDirectory(prefix="g3m-extract-") as temp_out:
                ArchiveExtractor.extract(archive_path, temp_out, is_cancelled)
                if is_cancelled and is_cancelled():
                    return
                total = 0
                for root, ignored_dirs, files in os.walk(temp_out):
                    if is_cancelled and is_cancelled():
                        return
                    del ignored_dirs
                    for f in files:
                        with contextlib.suppress(OSError):
                            total += os.path.getsize(os.path.join(root, f))
                if total > size_cap_bytes:
                    raise OSError("extracted_content_too_large")
                _move_tree_safely(temp_out, target_dir)
                _cleanup_extracted_archive(target_dir, is_game_installation)
        else:
            ArchiveExtractor.extract(archive_path, target_dir, is_cancelled)
            if not is_cancelled or not is_cancelled():
                _cleanup_extracted_archive(target_dir, is_game_installation)

def get_file_extension_from_url(url: str, content_type: str | None = None) -> str:
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path)).casefold()
    for extension in ARCHIVE_SUFFIXES:
        if filename.endswith(extension):
            return extension
    if content_type:
        content_type = content_type.partition(";")[0].strip().casefold()
        content_type_map = {
            "application/zip": ".zip",
            "application/x-rar-compressed": ".rar",
            "application/x-rar": ".rar",
            "application/x-7z-compressed": ".7z",
            "application/x-7z": ".7z",
            "application/x-tar": ".tar",
            "application/gzip": ".tar.gz",
            "application/x-gzip": ".tar.gz",
            "application/x-bzip2": ".tar.bz2",
            "application/x-xz": ".tar.xz",
            "application/x-lzma": ".lzma",
        }
        if content_type in content_type_map:
            return content_type_map[content_type]
    return ".zip"


def extract_any_archive(archive_path: str, target_dir: str) -> None:
    ArchiveExtractor.extract(archive_path, target_dir)


def extract_archive(
    archive_path: str,
    target_dir: str,
    fname: str | None = None,
    is_game_installation: bool = False,
    size_cap_bytes: int | None = None,
) -> None:
    ArchiveExtractor.extract_with_options(
        archive_path, target_dir, fname, is_game_installation, size_cap_bytes
    )


def extract_archive_content_root(
    archive_path: str,
    target_dir: str,
    *,
    size_cap_bytes: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    """Safely extract an archive and return its unwrapped content root."""
    ArchiveExtractor.extract_with_options(
        archive_path,
        target_dir,
        size_cap_bytes=size_cap_bytes,
        is_cancelled=is_cancelled,
    )
    return unwrap_single_directory_chain(target_dir)
