"""Choosing which saved versions travel with an exported or installed mod."""

import os
import zipfile
from collections.abc import Callable, Collection
from dataclasses import dataclass

from config.config import MOD_CONFIG_FILENAME, MOD_VERSIONS_DIR
from utils.mod.config import MOD_CONFIG_MAX_BYTES, ModConfigValidationError
from utils.mod.legacy_config_migration import migrate_legacy_config_bytes
from utils.mod.version_utils import (
    apply_version_zip,
    create_version_zip,
    get_unique_version_name,
    list_local_versions,
)


@dataclass(frozen=True)
class VersionSelection:
    """What the user kept from a mod package.

    ``versions`` holds names of archives in mod_versions to keep, ``keep_root`` says whether the
    package's own (root) files stay, and ``active`` names the archive to make the active one
    (``None`` keeps the root files active).
    """

    versions: frozenset[str] = frozenset()
    keep_root: bool = True
    active: str | None = None


ASK, ACTIVE_ONLY, ALL_VERSIONS = "ask", "active", "all"
VERSION_MODES = (ASK, ACTIVE_ONLY, ALL_VERSIONS)


def preset_export(mode: str, versions: list[dict]) -> frozenset[str]:
    """Saved versions to export without asking, for a non-``ask`` mode."""
    return frozenset(version["name"] for version in versions) if mode == ALL_VERSIONS else frozenset()


def preset_install(mode: str, versions: list[dict]) -> VersionSelection:
    """Install choice without asking: the package's own active version, with all or none of the rest."""
    return VersionSelection(preset_export(mode, versions))


def is_supported_config(raw: bytes) -> bool:
    """Whether a mod config is current or migrates cleanly to the current format."""
    try:
        migrate_legacy_config_bytes(raw)
    except (ModConfigValidationError, ValueError):
        return False
    return True


def _archive_config(path: str) -> bytes | None:
    """The mod_config.json nearest the root of a version archive, if it has one."""
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.filename.rsplit("/", 1)[-1] == MOD_CONFIG_FILENAME
            ]
            if not candidates:
                return None
            nearest = min(candidates, key=lambda info: info.filename.count("/"))
            if nearest.file_size > MOD_CONFIG_MAX_BYTES:
                return None
            return archive.read(nearest)
    except (OSError, zipfile.BadZipFile):
        return None


def split_valid_versions(versions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate saved versions that hold a supported mod config from the unusable ones."""
    valid, invalid = [], []
    for version in versions:
        raw = _archive_config(version["path"])
        (valid if raw is not None and is_supported_config(raw) else invalid).append(version)
    return valid, invalid


def describe_active_version(mod_dir: str) -> tuple[str, str]:
    """Return the mod's name and the label of the version currently in its root folder."""
    from utils.file_utils import load_json

    try:
        config = load_json(os.path.join(mod_dir, MOD_CONFIG_FILENAME))
    except (OSError, ValueError):
        config = {}
    name = str(config.get("name") or os.path.basename(mod_dir))
    return name, str(config.get("version") or "").strip()


def export_mod_archive(mod_dir: str, export_path: str, versions: Collection[str] = ()) -> None:
    """Zip the mod's active files, plus only the named archives from mod_versions."""
    wanted = {f"{name}.zip" for name in versions}
    versions_root = os.path.join(mod_dir, MOD_VERSIONS_DIR)
    with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, directories, files in os.walk(mod_dir):
            if os.path.normpath(root) == os.path.normpath(versions_root):
                directories.clear()
                files = [name for name in files if name in wanted]
            elif os.path.normpath(root) == os.path.normpath(mod_dir) and not wanted:
                directories[:] = [name for name in directories if name != MOD_VERSIONS_DIR]
            for name in files:
                path = os.path.join(root, name)
                if not os.path.islink(path):
                    archive.write(path, os.path.relpath(path, mod_dir))


def apply_version_selection(mod_dir: str, selection: VersionSelection, root_label: str) -> None:
    """Reduce a mod package to the chosen versions and make the chosen one active."""
    if selection.active is None and not selection.keep_root:
        raise ValueError("The active version cannot be left out")
    available = {version["name"]: version["path"] for version in list_local_versions(mod_dir)}
    for name, path in available.items():
        if name not in selection.versions:
            os.remove(path)
    if selection.active in available:
        if selection.keep_root:
            create_version_zip(
                mod_dir,
                mod_dir,
                get_unique_version_name(mod_dir, root_label or "default"),
                ignore_versions_dir=True,
            )
        apply_version_zip(mod_dir, available[selection.active])
        os.remove(available[selection.active])
    versions_dir = os.path.join(mod_dir, MOD_VERSIONS_DIR)
    if os.path.isdir(versions_dir) and not os.listdir(versions_dir):
        os.rmdir(versions_dir)


def resolve_package_versions(
    content_path: str,
    choose: Callable[[str, str, list[dict]], VersionSelection | None],
) -> bool:
    """Let the user trim the saved versions inside a mod package; False when they cancel.

    Only versions whose config is supported are offered; the others are dropped from the package.
    """
    config_path = os.path.join(content_path, MOD_CONFIG_FILENAME)
    saved = list_local_versions(content_path)
    if not saved or not os.path.isfile(config_path):
        return True
    with open(config_path, "rb") as source:
        if not is_supported_config(source.read(MOD_CONFIG_MAX_BYTES + 1)):
            return True
    versions, unusable = split_valid_versions(saved)
    for version in unusable:
        os.remove(version["path"])
    name, label = describe_active_version(content_path)
    if not versions:
        apply_version_selection(content_path, VersionSelection(), label or name)
        return True
    if (selection := choose(name, label, versions)) is None:
        return False
    apply_version_selection(content_path, selection, label or name)
    return True
