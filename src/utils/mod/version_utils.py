"""Utilities for managing mod versions and snapshots."""

import contextlib
import logging
import os
import shutil
import tempfile
import zipfile

from config.config import MOD_CONFIG_FILENAME, MOD_VERSIONS_DIR

logger = logging.getLogger(__name__)


def ensure_versions_dir(mod_folder: str) -> str:
    """Ensure the versions directory exists in the mod folder."""
    d = os.path.join(mod_folder, MOD_VERSIONS_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def create_version_zip(
    source_dir: str,
    mod_folder: str,
    version_name: str,
    ignore_versions_dir: bool = False,
) -> str:
    """Create a version zip from source directory."""
    versions_dir = ensure_versions_dir(mod_folder)
    safe_name = sanitize_version_name(version_name)
    zip_path = os.path.join(versions_dir, f"{safe_name}.zip")
    with tempfile.NamedTemporaryFile(dir=versions_dir, suffix=".tmp", delete=False) as handle:
        temporary = handle.name
    excluded = {os.path.normcase(os.path.abspath(path)) for path in (zip_path, temporary)}
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for root, dirs, files in os.walk(source_dir):
                if ignore_versions_dir and MOD_VERSIONS_DIR in dirs:
                    dirs.remove(MOD_VERSIONS_DIR)
                for fname in files:
                    full = os.path.join(root, fname)
                    if os.path.normcase(os.path.abspath(full)) not in excluded:
                        arcname = os.path.relpath(full, source_dir)
                        zf.write(full, arcname)
        os.replace(temporary, zip_path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(temporary)
    return zip_path


def get_unique_version_name(mod_folder: str, version_name: str) -> str:
    """Return a non-conflicting version name for the mod_versions directory."""
    versions_dir = ensure_versions_dir(mod_folder)
    if not os.path.exists(
        os.path.join(versions_dir, f"{sanitize_version_name(version_name)}.zip")
    ):
        return version_name
    index = 2
    while os.path.exists(
        os.path.join(
            versions_dir, f"{sanitize_version_name(f'{version_name} ({index})')}.zip"
        )
    ):
        index += 1
    return f"{version_name} ({index})"


def sanitize_version_name(name: str) -> str:
    """Sanitize version name for safe filename."""
    return (
        "".join(c if c.isalnum() or c in " _-." else "_" for c in name).strip()
        or "version"
    )


def list_local_versions(mod_folder: str) -> list[dict]:
    versions_dir = os.path.join(mod_folder, MOD_VERSIONS_DIR)
    if not os.path.isdir(versions_dir):
        return []
    result = []
    for fname in sorted(os.listdir(versions_dir)):
        fpath = os.path.join(versions_dir, fname)
        if not os.path.isfile(fpath) or not fname.lower().endswith(".zip"):
            continue
        try:
            stat = os.stat(fpath)
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size, mtime = 0, 0
        result.append(
            {
                "name": os.path.splitext(fname)[0],
                "filename": fname,
                "path": fpath,
                "size": size,
                "mtime": mtime,
            }
        )
    return result


def clear_mod_folder(mod_folder: str):
    for item in os.listdir(mod_folder):
        if item == MOD_VERSIONS_DIR:
            continue
        path = os.path.join(mod_folder, item)
        if os.path.isdir(path):
            try:
                shutil.rmtree(path)
            except OSError as e:
                logger.warning("mod_versions: failed to remove %s: %s", path, e)
        else:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("mod_versions: failed to remove %s: %s", path, e)


def resolve_content_path(temp_dir: str) -> str:
    """Resolve single directory layers and convert deltamod if present."""
    from utils.file_utils import has_deltamod_info_file

    content_path = temp_dir
    contents = os.listdir(temp_dir)
    if len(contents) == 1:
        single = os.path.join(temp_dir, contents[0])
        if os.path.isdir(single):
            content_path = single
    files_in_root = os.listdir(content_path)
    if has_deltamod_info_file(files_in_root):
        from adapters.deltamod_adapter import DeltamodConverter

        converter = DeltamodConverter(content_path, temp_dir)
        result = converter.convert()
        if result and os.path.isdir(result):
            content_path = result
    return content_path


def apply_version_zip(mod_folder: str, zip_path: str):
    """Apply version zip, converting deltamod contents if needed."""
    from utils.file_utils import load_json
    from utils.mod.config import write_mod_config
    from utils.mod.legacy_config_migration import migrate_legacy_config

    temp_dir = tempfile.mkdtemp(prefix="mv_apply_")
    backup_dir = tempfile.mkdtemp(prefix="mv_backup_")
    staged_current = False
    try:
        current_config = load_json(os.path.join(mod_folder, MOD_CONFIG_FILENAME))
        current_metadata = current_config.get("metadata", {})
        current_mod_id = (
            current_metadata.get("id") if isinstance(current_metadata, dict) else None
        ) or current_config.get("id")
        from utils.mod.archive import materialize_archive

        materialize_archive(zip_path, temp_dir)
        content_path = resolve_content_path(temp_dir)
        from utils.file_utils import normalize_mod_package

        package = normalize_mod_package(content_path, require_mod_config=True)
        staged_config_path = package["mod_config_path"]
        if not staged_config_path:
            raise FileNotFoundError("Staged mod configuration is missing")
        staged_config = migrate_legacy_config(
            load_json(staged_config_path),
            mod_root_path=content_path,
        )
        if current_mod_id:
            staged_config["id"] = current_mod_id
        write_mod_config(staged_config_path, staged_config)
        for item in os.listdir(mod_folder):
            if item != MOD_VERSIONS_DIR:
                shutil.move(
                    os.path.join(mod_folder, item), os.path.join(backup_dir, item)
                )
        staged_current = True
        for item in os.listdir(content_path):
            src = os.path.join(content_path, item)
            dst = os.path.join(mod_folder, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    except Exception:
        if staged_current:
            clear_mod_folder(mod_folder)
            for item in os.listdir(backup_dir):
                shutil.move(
                    os.path.join(backup_dir, item), os.path.join(mod_folder, item)
                )
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)
