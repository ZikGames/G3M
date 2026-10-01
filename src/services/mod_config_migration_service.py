"""Atomic operation migration for G3M-managed mod folders and version snapshots."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from config.config import MOD_CONFIG_FILENAME, MOD_VERSIONS_DIR
from utils.mod.archive import (
    ArchiveMember,
    archive_format,
    archive_write_supported,
    list_archive_members,
    materialize_archive,
    rebuild_archive,
)
from utils.mod.config import read_mod_config_bytes
from utils.mod.legacy_config_migration import (
    migrate_legacy_config_bytes,
    migrate_legacy_config_file,
)


@dataclass(frozen=True, slots=True)
class ModConfigMigrationIssue:
    """A managed config that could not safely be migrated."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ModConfigMigrationReport:
    """Results for one profile's owned mod directory."""

    configs: tuple[Path, ...]
    snapshots: tuple[Path, ...]
    issues: tuple[ModConfigMigrationIssue, ...]


def _managed_config_path(mod_folder: Path) -> Path | None:
    direct = mod_folder / MOD_CONFIG_FILENAME
    if direct.is_symlink():
        return None
    if direct.is_file():
        return direct
    nested = [
        child / MOD_CONFIG_FILENAME
        for child in mod_folder.iterdir()
        if child.is_dir() and not child.is_symlink() and (child / MOD_CONFIG_FILENAME).is_file()
    ]
    return nested[0] if len(nested) == 1 and not nested[0].is_symlink() else None


def _snapshot_config_name(members: tuple[ArchiveMember, ...]) -> str | None:
    matches = [
        member.name.rstrip("/")
        for member in members
        if not member.directory
        and posixpath.basename(member.name.rstrip("/")) == MOD_CONFIG_FILENAME
    ]
    return matches[0] if len(matches) == 1 else None


def _snapshot_directories(
    members: tuple[ArchiveMember, ...], config_name: str
) -> set[str]:
    prefix = posixpath.dirname(config_name)
    prefix = f"{prefix}/" if prefix else ""
    directories: set[str] = set()
    for member in members:
        name = member.name.rstrip("/")
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix) :]
        if not relative or relative == MOD_CONFIG_FILENAME:
            continue
        parts = relative.split("/")
        limit = len(parts) if member.directory else len(parts) - 1
        for index in range(1, limit + 1):
            directories.add("/".join(parts[:index]))
    return directories


def _rewrite_snapshot(path: Path) -> bool:
    if not archive_write_supported(path):
        raise ValueError("snapshot archive format does not support writing")
    members = list_archive_members(path)
    config_name = _snapshot_config_name(members)
    if config_name is None:
        raise ValueError("snapshot must contain exactly one mod_config.json")
    with TemporaryDirectory(prefix=f".{path.name}.", dir=path.parent) as temporary:
        materialized = Path(temporary)
        materialize_archive(path, materialized)
        config_path = materialized.joinpath(*config_name.split("/"))
        raw = read_mod_config_bytes(config_path)
        migrated = migrate_legacy_config_bytes(
            raw,
            mod_root_path=config_path.parent,
            legacy_directory_paths=_snapshot_directories(members, config_name),
        )
        if json.loads(raw) == migrated:
            return False
        config_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rebuild_archive(path, materialized)
    return True


def _snapshot_paths(mod_folder: Path) -> Iterator[Path]:
    versions = mod_folder / MOD_VERSIONS_DIR
    if not versions.is_dir() or versions.is_symlink():
        return
    yield from sorted(
        (path for path in versions.iterdir() if path.is_file() and archive_format(path)),
        key=lambda path: path.name.casefold(),
    )


def migrate_managed_mods(mods_dir: str | Path) -> ModConfigMigrationReport:
    """Migrate only owned profile copies; external imports are never touched."""
    root = Path(mods_dir)
    configs: list[Path] = []
    snapshots: list[Path] = []
    issues: list[ModConfigMigrationIssue] = []
    try:
        mod_folders = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: path.name.casefold(),
        )
    except OSError as error:
        return ModConfigMigrationReport((), (), (ModConfigMigrationIssue(root, str(error)),))

    for mod_folder in mod_folders:
        try:
            config_path = _managed_config_path(mod_folder)
        except OSError as error:
            issues.append(ModConfigMigrationIssue(mod_folder, str(error)))
            continue
        if config_path is not None:
            try:
                raw = read_mod_config_bytes(config_path)
                migrate_legacy_config_file(config_path)
                if config_path.read_bytes() != raw:
                    configs.append(config_path)
            except (OSError, ValueError) as error:
                issues.append(ModConfigMigrationIssue(config_path, str(error)))
        try:
            snapshot_paths = tuple(_snapshot_paths(mod_folder))
        except OSError as error:
            issues.append(ModConfigMigrationIssue(mod_folder / MOD_VERSIONS_DIR, str(error)))
            continue
        for snapshot_path in snapshot_paths:
            try:
                if _rewrite_snapshot(snapshot_path):
                    snapshots.append(snapshot_path)
            except (OSError, ValueError) as error:
                issues.append(ModConfigMigrationIssue(snapshot_path, str(error)))

    return ModConfigMigrationReport(tuple(configs), tuple(snapshots), tuple(issues))
