"""Helpers for discovering and reading mod README files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from config.config import (
    MOD_CONFIG_FILENAME,
    MOD_DOCUMENTATION_EXTENSIONS,
    MOD_HTML_EXTENSIONS,
    MOD_MARKDOWN_EXTENSIONS,
    MOD_PDF_EXTENSIONS,
    MOD_README_ENCODINGS,
)
from utils.mod.archive import (
    ArchiveValidationError,
    list_archive_members,
    split_archive_virtual_path,
)
from utils.mod.config import iter_mod_config_leaves, load_mod_config


def _load_config(folder: Path) -> dict[str, object]:
    config_path = folder / MOD_CONFIG_FILENAME
    if not config_path.is_file():
        return {}
    try:
        return load_mod_config(config_path)
    except (OSError, ValueError):
        return {}


def _iter_operation_info_sources(entries: list[object]) -> Iterator[str]:
    for _group_path, entry in iter_mod_config_leaves(entries):
        source = entry.get("source")
        if entry.get("type") == "info" and isinstance(source, str):
            yield source


def _mod_local_path(folder: Path, source: str) -> Path | None:
    prefix = "${mod_path}/"
    if not source.startswith(prefix) or source.endswith("/"):
        return None
    path = folder.joinpath(*source[len(prefix) :].split("/"))
    try:
        path.resolve(strict=False).relative_to(folder.resolve())
    except ValueError:
        return None
    return path


def _expand_mod_path_placeholder(source: str, placeholders: object) -> str:
    if source.startswith("${mod_path}/") or not source.startswith("${"):
        return source
    name, separator, suffix = source[2:].partition("}")
    value = placeholders.get(name) if isinstance(placeholders, dict) else None
    return f"{value}{suffix}" if separator and isinstance(value, str) and value.startswith("${mod_path}/") else source


def _archive_info_file(path: Path) -> str | None:
    try:
        virtual = split_archive_virtual_path(path)
        if virtual is None or virtual.directory or not virtual.archive.is_file():
            return None
        members = list_archive_members(virtual.archive)
    except (ArchiveValidationError, OSError, ValueError):
        return None
    if not any(
        member.name == virtual.member and not member.directory and not member.link
        for member in members
    ):
        return None
    return f"{virtual.archive}/{virtual.member}"


def _listed_info_files(folder: Path) -> list[str]:
    config = _load_config(folder)
    files = config.get("files")
    if not isinstance(files, list):
        return []
    placeholders = config.get("placeholders")
    result = []
    for source in _iter_operation_info_sources(files):
        path = _mod_local_path(
            folder, _expand_mod_path_placeholder(source, placeholders)
        )
        if path is None or path.suffix.casefold() not in MOD_DOCUMENTATION_EXTENSIONS:
            continue
        if path.is_file() and not path.is_symlink():
            result.append(str(path))
            continue
        if archive_path := _archive_info_file(path):
            result.append(archive_path)
    return result


def _all_info_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.name != MOD_CONFIG_FILENAME
            and path.suffix.casefold() in MOD_DOCUMENTATION_EXTENSIONS
        ),
        key=lambda path: path.relative_to(folder).as_posix().casefold(),
    )


def find_mod_unlisted_readme_files(mod_folder: str | None) -> list[str]:
    """Return unlisted readable files in stable relative-path order."""
    if not mod_folder:
        return []
    folder = Path(mod_folder)
    if not folder.is_dir():
        return []
    listed = {
        Path(path).resolve()
        for path in _listed_info_files(folder)
        if split_archive_virtual_path(path) is None
    }
    return [str(path) for path in _all_info_files(folder) if path.resolve() not in listed]


def find_mod_readme_files(mod_folder: str | None, *, include_unlisted: bool = True) -> list[str]:
    """Return listed info files first and optionally append unlisted files."""
    if not mod_folder:
        return []
    folder = Path(mod_folder)
    if not folder.is_dir():
        return []
    listed = _listed_info_files(folder)
    return listed + (find_mod_unlisted_readme_files(mod_folder) if include_unlisted else [])


def read_mod_readme(file_path: str) -> str:
    """Read a README file with a few safe encoding fallbacks."""
    path = Path(file_path)
    for encoding in MOD_README_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def is_markdown_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in MOD_MARKDOWN_EXTENSIONS


def is_html_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in MOD_HTML_EXTENSIONS


def is_pdf_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in MOD_PDF_EXTENSIONS
