"""Utilities for parsing, migrating, and canonicalizing mod config data."""

from __future__ import annotations

import json
import os
import posixpath
import re
import unicodedata
from collections.abc import Collection, Mapping
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from config.config import CYOP_AFOM_TAG, MOD_DOCUMENTATION_EXTENSIONS
from models.game_modes import get_all_games
from services.migration_service import migrate_legacy_chapter_id
from utils.file_utils import normalize_chapter_id
from utils.mod.config import (
    MOD_CONFIG_MAX_BYTES,
    MOD_CONFIG_MAX_DISPLAY_CHARS,
    MOD_CONFIG_MAX_JSON_DEPTH,
    ConfigValidationIssue,
    ModConfigValidationError,
    _DuplicateJsonKeyError,
    _json_depth,
    _reject_duplicate_keys,
    _write_config_payload,
    parse_mod_config,
    read_mod_config_bytes,
)
from utils.mod.config import MOD_CONFIG_VERSION as CURRENT_MOD_CONFIG_VERSION
from utils.mod.operation_plan import section_target_root

_DRIVE_PATH_RE = re.compile(r"[A-Za-z]:/")
_LEGACY_ARCHIVE_SUFFIXES = (
    ".tar.lzma", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2",
    ".txz", ".zip", ".7z", ".rar", ".tar", ".lzma",
)
_LEGACY_PATCH_SUFFIXES = (".xdelta", ".vcdiff", ".g3mpatch", ".csx")
_LEGACY_DESCRIPTION_KEY = "tagline"
_LEGACY_ICON_KEY = "icon_url"
_LEGACY_ROOT_ICON_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp")
_LEGACY_MOD_ID_KEYS = ("key", "mod_key")
_LEGACY_HOMEPAGE_KEYS = ("homepage", "external_url", "external_link", "site", "url")
_EXTRA_FILE_TARGET_GAME_FOLDER = "game_folder"
_EXTRA_FILE_TARGET_GAME_DATA_FOLDER = "game_data_folder"
_EXTRA_FILE_TARGET_NONE = "none"
_EXTRA_FILE_TARGET_CUSTOM = "custom"
_EXTRA_FILE_TARGETS = frozenset(
    {
        _EXTRA_FILE_TARGET_GAME_FOLDER,
        _EXTRA_FILE_TARGET_GAME_DATA_FOLDER,
        _EXTRA_FILE_TARGET_NONE,
        _EXTRA_FILE_TARGET_CUSTOM,
    }
)
_LEGACY_EXTRA_FILE_TARGETS = {
    "install": _EXTRA_FILE_TARGET_GAME_FOLDER,
    "data": _EXTRA_FILE_TARGET_GAME_DATA_FOLDER,
    "dependency": _EXTRA_FILE_TARGET_NONE,
}


def normalize_extra_file_target(value: object) -> str:
    target = str(value or "").strip().lower()
    target = _LEGACY_EXTRA_FILE_TARGETS.get(target, target)
    return target if target in _EXTRA_FILE_TARGETS else _EXTRA_FILE_TARGET_NONE


def build_extra_file_entry(
    path: str,
    target: str = _EXTRA_FILE_TARGET_GAME_FOLDER,
    target_path: str = "",
) -> dict[str, str]:
    entry = {"file_path": path, "target": normalize_extra_file_target(target)}
    if entry["target"] == _EXTRA_FILE_TARGET_CUSTOM and target_path.strip():
        entry["target_path"] = target_path.strip()
    return entry


def infer_legacy_extra_file_target(game: str, path: str, target: str) -> str:
    if target != _EXTRA_FILE_TARGET_GAME_FOLDER:
        return target
    normalized_path = str(path or "").replace("\\", "/").strip("/").lower()
    special_name = {"pizzatower": "towers", "frickbears3": "addons"}.get(
        str(game or "").lower()
    )
    if not special_name:
        return target
    if normalized_path == special_name or normalized_path.startswith(f"{special_name}/"):
        return _EXTRA_FILE_TARGET_GAME_DATA_FOLDER
    archive_stem = (
        normalized_path[:-7]
        if normalized_path.endswith(".tar.gz")
        else normalized_path[:-9]
        if normalized_path.endswith(".tar.lzma")
        else Path(normalized_path).stem
    )
    if "/" not in normalized_path and archive_stem == special_name:
        return _EXTRA_FILE_TARGET_GAME_DATA_FOLDER
    return target


def migrate_mod_config_legacy_fields(config_data: dict[str, Any]) -> bool:
    if not isinstance(config_data, dict):
        return False
    changed = False
    metadata = config_data.get("metadata")
    if isinstance(metadata, dict) and migrate_mod_config_legacy_fields(metadata):
        changed = True

    description_value = config_data.get("description")
    if description_value in (None, "") and _LEGACY_DESCRIPTION_KEY in config_data:
        description_value = config_data.get(_LEGACY_DESCRIPTION_KEY)
    icon_value = config_data.get("icon")
    if icon_value in (None, "") and _LEGACY_ICON_KEY in config_data:
        icon_value = config_data.get(_LEGACY_ICON_KEY)
    homepage_value = config_data.get("homepage")
    if homepage_value in (None, ""):
        homepage_value = next(
            (
                config_data[legacy_key]
                for legacy_key in _LEGACY_HOMEPAGE_KEYS
                if config_data.get(legacy_key) not in (None, "")
            ),
            homepage_value,
        )
    if not config_data.get("id"):
        legacy_id = next(
            (
                config_data[legacy_key].strip()
                for legacy_key in _LEGACY_MOD_ID_KEYS
                if isinstance(config_data.get(legacy_key), str)
                and config_data[legacy_key].strip()
            ),
            None,
        )
        if legacy_id:
            config_data["id"] = legacy_id
            changed = True

    normalized_items: list[tuple[str, Any]] = []
    seen_keys: set[str] = set()
    for key, value in config_data.items():
        if key == _LEGACY_DESCRIPTION_KEY:
            key, value = "description", description_value
        elif key == _LEGACY_ICON_KEY:
            key, value = "icon", icon_value
        elif key in _LEGACY_MOD_ID_KEYS:
            key, value = "id", config_data.get("id", value)
        elif key in _LEGACY_HOMEPAGE_KEYS:
            key, value = "homepage", homepage_value
        elif key == "description":
            value = description_value
        elif key == "icon":
            value = icon_value
        elif key == "homepage":
            value = homepage_value
        elif key == "files" and isinstance(value, dict):
            migrated_files = {}
            for file_key, file_info in value.items():
                migrated_info = dict(file_info) if isinstance(file_info, dict) else file_info
                if isinstance(migrated_info, dict):
                    data_file_path = migrated_info.pop("data_file_url", None)
                    if data_file_path not in (None, "") and not migrated_info.get("data_file_path"):
                        migrated_info["data_file_path"] = data_file_path
                    extra_files = migrated_info.get("extra_files")
                    normalized_extra_files = []
                    if isinstance(extra_files, list):
                        for extra_file in extra_files:
                            file_path = (
                                extra_file
                                if isinstance(extra_file, str)
                                else extra_file.get("file_path") or extra_file.get("url")
                                if isinstance(extra_file, dict)
                                else None
                            )
                            if not file_path:
                                continue
                            target = (
                                extra_file.get("target") or extra_file.get("status") or _EXTRA_FILE_TARGET_GAME_FOLDER
                                if isinstance(extra_file, dict)
                                else _EXTRA_FILE_TARGET_GAME_FOLDER
                            )
                            target_path = str(extra_file.get("target_path") or "") if isinstance(extra_file, dict) else ""
                            normalized_extra_files.append(build_extra_file_entry(file_path, target, target_path))
                    elif isinstance(extra_files, dict):
                        normalized_extra_files = [
                            build_extra_file_entry(file_path)
                            for filenames in extra_files.values()
                            if isinstance(filenames, list)
                            for file_path in filenames
                            if file_path
                        ]
                    if normalized_extra_files:
                        migrated_info["extra_files"] = normalized_extra_files
                    elif extra_files not in (None, [], {}):
                        migrated_info["extra_files"] = []
                migrated_files[migrate_legacy_chapter_id(file_key)] = migrated_info
            changed |= migrated_files != value
            value = migrated_files
        if key in seen_keys:
            changed = True
            continue
        seen_keys.add(key)
        normalized_items.append((key, value))
    if list(config_data.items()) != normalized_items:
        changed = True
    if changed:
        config_data.clear()
        config_data.update(normalized_items)
    return changed

MOD_CONFIG_VERSION = "1.0.0"
MOD_ALLOWED_TAGS = ("textedit", "customization", "gameplay", "other", CYOP_AFOM_TAG)
MOD_FIELD_LIMITS = {
    "id": 50,
    "name": 50,
    "author": 50,
    "version": 20,
    "game": 30,
    "description": 200,
    "homepage": 200,
    "icon": 200,
    "game_version": 20,
    "file_value": 1000,
}
MOD_METADATA_KEY_ORDER = (
    "id",
    "name",
    "version",
    "author",
    "description",
    "homepage",
    "icon",
    "game",
    "game_version",
    "tags",
)
MOD_INFO_FILE_VISIBILITY = ("show", "hide", "remove")
MOD_RUNTIME_KEY_ORDER = (
    "config_version",
    *MOD_METADATA_KEY_ORDER,
    "info_files",
    "files",
)


def _trim_string(value, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _normalize_extra_file_path(path_value: str) -> str:
    raw = str(path_value or "")
    if not raw:
        return ""
    preserve_trailing_slash = raw.rstrip().endswith(("/", "\\"))
    normalized = raw.replace("\\", "/").strip()
    if not normalized:
        return ""
    if preserve_trailing_slash:
        normalized = normalized.rstrip("/")
        return f"{normalized}/" if normalized else ""
    return normalized


def _normalize_homepage(value) -> str:
    url = _trim_string(value, MOD_FIELD_LIMITS["homepage"])
    if not url:
        return ""
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _sanitize_tags(tags_raw) -> list[str]:
    if not isinstance(tags_raw, list):
        tags_raw = [tags_raw] if tags_raw else []
    result: list[str] = []
    for tag in tags_raw:
        raw_tag = _trim_string(tag, 100)
        normalized = (
            CYOP_AFOM_TAG
            if raw_tag.casefold() == CYOP_AFOM_TAG.casefold()
            else raw_tag.lower()
        )
        if normalized in MOD_ALLOWED_TAGS and normalized not in result:
            result.append(normalized)
    return result


def _sanitize_extra_files(extra_files_raw, game: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for entry in parse_extra_file_entries_raw(extra_files_raw):
        extra_file = entry["file_path"]
        file_path = _normalize_extra_file_path(
            _trim_string(extra_file, MOD_FIELD_LIMITS["file_value"])
        )
        if not file_path:
            continue
        target = infer_legacy_extra_file_target(game, file_path, entry["target"])
        value = build_extra_file_entry(
            file_path,
            target,
            _trim_string(entry.get("target_path"), MOD_FIELD_LIMITS["file_value"]),
        )
        if value not in result:
            result.append(value)
    return result


def _sanitize_info_files(info_files_raw) -> dict[str, str]:
    """Sanitize info_files config dict. Info files must be file paths, not directories."""
    result: dict[str, str] = {}
    if not isinstance(info_files_raw, dict):
        return result
    for raw_path, raw_visibility in info_files_raw.items():
        file_path = _normalize_extra_file_path(
            _trim_string(raw_path, MOD_FIELD_LIMITS["file_value"])
        ).rstrip("/")
        if not file_path:
            continue
        visibility = str(raw_visibility or "").strip().lower()
        result[file_path] = (
            visibility if visibility in MOD_INFO_FILE_VISIBILITY else "show"
        )
    return result


def _get_metadata_value(config_data: dict, key: str):
    metadata = config_data.get("metadata")
    if key in config_data and config_data.get(key) not in (None, "", [], {}):
        return config_data.get(key)
    if isinstance(metadata, dict):
        return metadata.get(key)
    return config_data.get(key)


def _get_legacy_layout_prefixes(mod_root_path: str | None) -> list[str]:
    if not mod_root_path or not os.path.isdir(mod_root_path):
        return []
    known_game_dirs = {game.game_id for game in get_all_games()}
    candidates: list[str] = []
    for entry in sorted(os.listdir(mod_root_path)):
        entry_path = os.path.join(mod_root_path, entry)
        if not os.path.isdir(entry_path):
            continue
        if (
            entry.startswith("chapter_")
            or entry in {"demo", "menu", "universal"}
            or entry in known_game_dirs
        ):
            candidates.append(entry)
    return candidates


def _migrate_legacy_layout_path(path_value: str, mod_root_path: str | None) -> str:
    preserve_trailing_slash = str(path_value or "").rstrip().endswith(("/", "\\"))
    normalized_path = _normalize_extra_file_path(path_value)
    if preserve_trailing_slash and normalized_path.endswith("/"):
        lookup_path = normalized_path[:-1]
    else:
        lookup_path = normalized_path
    if not normalized_path or not mod_root_path or os.path.isabs(normalized_path):
        return normalized_path
    direct_path = os.path.join(mod_root_path, lookup_path)
    if os.path.exists(direct_path):
        return normalized_path
    matches = []
    for prefix in _get_legacy_layout_prefixes(mod_root_path):
        candidate = os.path.join(mod_root_path, prefix, lookup_path)
        if os.path.exists(candidate):
            migrated = f"{prefix}/{lookup_path}" if lookup_path else prefix
            if preserve_trailing_slash:
                migrated = migrated.rstrip("/") + "/"
            matches.append(migrated)
    return matches[0] if len(matches) == 1 else normalized_path


def _sanitize_files(
    files_data: dict,
    game: str,
    mod_root_path: str | None = None,
) -> dict[str, dict]:
    normalized: dict[str, dict] = {}
    if not isinstance(files_data, dict):
        return normalized
    for raw_file_key, ch_info in files_data.items():
        if not isinstance(ch_info, dict):
            continue
        file_key = _trim_string(
            normalize_chapter_id(raw_file_key, game),
            MOD_FIELD_LIMITS["file_value"],
        )
        if not file_key:
            continue
        entry: dict[str, object] = {}
        description = _trim_string(
            ch_info.get("description"), MOD_FIELD_LIMITS["description"]
        )
        if description:
            entry["description"] = description
        data_file_path = _trim_string(
            ch_info.get("data_file_path") or ch_info.get("data_file_url"),
            MOD_FIELD_LIMITS["file_value"],
        )
        if data_file_path:
            entry["data_file_path"] = _migrate_legacy_layout_path(
                data_file_path, mod_root_path
            )
        extra_files = _sanitize_extra_files(ch_info.get("extra_files", []), game)
        if extra_files:
            entry["extra_files"] = []
            for extra_file in extra_files:
                if isinstance(extra_file, dict):
                    entry["extra_files"].append(
                        {
                            **extra_file,
                            "file_path": _migrate_legacy_layout_path(
                                extra_file["file_path"], mod_root_path
                            ),
                        }
                    )
            if not entry["extra_files"]:
                entry.pop("extra_files", None)
        normalized[file_key] = entry
    return normalized


def normalize_mod_config_data(
    config_data: dict,
    mod_root_path: str | None = None,
) -> bool:
    """Normalize config keys and values to the canonical 1.0.0 schema."""
    if not isinstance(config_data, dict):
        return False
    if config_data.get("config_version") == "2.0.0":
        return False
    changed = migrate_mod_config_legacy_fields(config_data)
    canonical = {
        "config_version": MOD_CONFIG_VERSION,
        "id": _trim_string(
            _get_metadata_value(config_data, "id"), MOD_FIELD_LIMITS["id"]
        ),
        "name": _trim_string(
            _get_metadata_value(config_data, "name"), MOD_FIELD_LIMITS["name"]
        ),
        "version": _trim_string(
            _get_metadata_value(config_data, "version"), MOD_FIELD_LIMITS["version"]
        )
        or "1.0.0",
        "author": _trim_string(
            _get_metadata_value(config_data, "author"), MOD_FIELD_LIMITS["author"]
        ),
        "description": _trim_string(
            _get_metadata_value(config_data, "description"),
            MOD_FIELD_LIMITS["description"],
        ),
        "homepage": _normalize_homepage(_get_metadata_value(config_data, "homepage")),
        "icon": _trim_string(
            _get_metadata_value(config_data, "icon"), MOD_FIELD_LIMITS["icon"]
        ),
        "game": _trim_string(
            _get_metadata_value(config_data, "game"), MOD_FIELD_LIMITS["game"]
        )
        or "deltarune",
        "game_version": _trim_string(
            _get_metadata_value(config_data, "game_version"),
            MOD_FIELD_LIMITS["file_value"],
        ),
        "tags": _sanitize_tags(_get_metadata_value(config_data, "tags")),
        "info_files": _sanitize_info_files(config_data.get("info_files")),
        "files": {},
    }
    canonical["files"] = _sanitize_files(
        config_data.get("files", {}),
        canonical["game"],
        mod_root_path,
    )
    ordered = {
        key: canonical[key]
        for key in MOD_RUNTIME_KEY_ORDER
        if canonical[key] not in (None, "", [], {})
    }
    if list(config_data.items()) != list(ordered.items()):
        changed = True
        config_data.clear()
        config_data.update(ordered)
    return changed


def parse_extra_file_entries_raw(
    extra_files_raw,
    mod_root_path: str | None = None,
) -> list[dict[str, str]]:
    """Parse extra files while preserving their deployment target."""
    result: list[dict[str, str]] = []
    if not extra_files_raw:
        return result

    def _resolve_runtime_path(file_path: str) -> str:
        normalized_path = _normalize_extra_file_path(file_path)
        if not normalized_path or not mod_root_path or os.path.isabs(normalized_path):
            return normalized_path
        preserve_trailing_slash = normalized_path.endswith("/")
        join_path = normalized_path[:-1] if preserve_trailing_slash else normalized_path
        resolved = os.path.normpath(os.path.join(mod_root_path, join_path))
        return resolved + os.sep if preserve_trailing_slash else resolved

    def _append_entry(
        file_path: str,
        target: object = _EXTRA_FILE_TARGET_GAME_FOLDER,
        target_path: object = "",
    ) -> None:
        resolved_path = _resolve_runtime_path(file_path)
        if resolved_path:
            entry = {
                "file_path": resolved_path,
                "target": normalize_extra_file_target(target),
            }
            if entry["target"] == _EXTRA_FILE_TARGET_CUSTOM:
                entry["target_path"] = _trim_string(
                    target_path, MOD_FIELD_LIMITS["file_value"]
                )
            result.append(entry)

    if isinstance(extra_files_raw, list):
        for ef_data in extra_files_raw:
            if isinstance(ef_data, dict):
                file_path = ef_data.get("file_path") or ef_data.get("url", "")
                if isinstance(file_path, str) and file_path:
                    _append_entry(
                        file_path,
                        ef_data.get("target")
                        or ef_data.get("status")
                        or _EXTRA_FILE_TARGET_GAME_FOLDER,
                        ef_data.get("target_path"),
                    )
            elif isinstance(ef_data, str):
                _append_entry(ef_data)
    elif isinstance(extra_files_raw, dict):
        for filenames in extra_files_raw.values():
            if isinstance(filenames, list):
                for filename in filenames:
                    _append_entry(filename)
    return result


def _normalized_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/")


def _is_absolute_path(value: str) -> bool:
    return bool(_DRIVE_PATH_RE.match(value) or value.startswith("/"))


def _source_path(value: object, *, directory: bool = False) -> str:
    normalized = _normalized_path(value)
    if not _is_absolute_path(normalized):
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = f"${{mod_path}}/{normalized}"
    return f"{normalized.rstrip('/')}/" if directory else normalized.rstrip("/")


def find_legacy_root_icon(mod_root_path: str | Path | None) -> str | None:
    """Return the old implicit root icon name, if this mod has one."""
    if not mod_root_path:
        return None
    root = Path(mod_root_path)
    return next(
        (
            candidate.name
            for extension in _LEGACY_ROOT_ICON_EXTENSIONS
            if (candidate := root / f"_icon{extension}").is_file()
        ),
        None,
    )


def _migrate_icon(icon: object, mod_root_path: str | Path | None) -> str | None:
    value = _normalized_path(icon)
    if value.startswith(("http://", "https://")):
        return value
    if value and _is_absolute_path(value):
        if mod_root_path:
            root = os.path.abspath(os.fspath(mod_root_path))
            candidate = os.path.abspath(value)
            try:
                if os.path.commonpath((root, candidate)) == root:
                    value = os.path.relpath(candidate, root).replace("\\", "/")
                else:
                    value = ""
            except ValueError:
                value = ""
        else:
            value = ""
    if value and ".." not in value.split("/"):
        return _source_path(value)
    if root_icon := find_legacy_root_icon(mod_root_path):
        return _source_path(root_icon)
    return None


def _deltarune_chapter_number(section_id: str) -> str | None:
    prefix, separator, suffix = section_id.rpartition("_")
    return suffix if separator and prefix == "deltarune" and suffix.isdecimal() else None


def _extra_relative_path(game: str, chapter_id: str, path: str) -> str:
    normalized = path.strip("/")
    candidates: list[str] = []
    chapter_number = _deltarune_chapter_number(chapter_id)
    if chapter_number is not None:
        candidates.extend((f"chapter_{chapter_number}", f"chapter{chapter_number}_windows", f"chapter{chapter_number}_mac"))
    if game and normalized.startswith(f"{game}/"):
        candidates.append(game)
    for candidate in candidates:
        if normalized == candidate:
            return ""
        prefix = f"{candidate}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _is_directory(
    path: str,
    mod_root_path: str | Path | None,
    directory_paths: Collection[str] = (),
) -> bool:
    return (
        path.endswith("/")
        or path.rstrip("/") in directory_paths
        or bool(mod_root_path and not _is_absolute_path(path) and os.path.isdir(os.path.join(os.fspath(mod_root_path), path)))
    )


def _target_root(target: str, chapter_root: str, custom_target: str) -> str | None:
    if target == "game_folder":
        return chapter_root
    if target == "game_data_folder":
        return "${game_data_path}"
    if target != "custom":
        return None
    normalized = _normalized_path(custom_target)
    return normalized.rstrip("/") if normalized and (_is_absolute_path(normalized) or PureWindowsPath(normalized).is_absolute()) else None


def _extra_operation(
    *,
    game: str,
    chapter_id: str,
    stored_path: object,
    target: object,
    target_path: object,
    mod_root_path: str | Path | None,
    directory_paths: Collection[str],
) -> dict[str, object] | None:
    normalized = _normalized_path(stored_path)
    if not normalized or ".." in normalized.split("/"):
        return None
    directory = _is_directory(normalized, mod_root_path, directory_paths)
    target_name = {"install": "game_folder", "data": "game_data_folder", "dependency": "none"}.get(
        str(target or "game_folder").strip().casefold(), str(target or "game_folder").strip().casefold()
    )
    root = _target_root(target_name, section_target_root(game, chapter_id), str(target_path or ""))
    if root is None:
        return None
    relative = _extra_relative_path(game, chapter_id, normalized)
    if directory:
        target_value = f"{root}/{relative.rstrip('/')}/" if relative else f"{root}/"
        return {"source": _source_path(normalized, directory=True), "target": target_value, "type": "extract"}
    if normalized.casefold().endswith(_LEGACY_ARCHIVE_SUFFIXES):
        parent = posixpath.dirname(relative)
        return {"source": _source_path(normalized), "target": f"{root}/{parent}/" if parent else f"{root}/", "type": "extract"}
    if not relative:
        return None
    for suffix in _LEGACY_PATCH_SUFFIXES:
        if relative.casefold().endswith(suffix):
            return {
                "source": _source_path(normalized),
                "target": f"{root}/{relative[: -len(suffix)]}",
                "type": "patch",
            }
    return {"source": _source_path(normalized), "target": f"{root}/{relative}", "type": "overwrite"}


def migrate_legacy_config(
    legacy_config: Mapping[str, object],
    *,
    mod_root_path: str | Path | None = None,
    legacy_directory_paths: Collection[str] = (),
) -> dict[str, object]:
    """Convert a historical config to the one current operation format."""
    if not isinstance(legacy_config, Mapping):
        raise ModConfigValidationError((ConfigValidationIssue("type", "$", "legacy config must be an object"),))
    if _json_depth(legacy_config) > MOD_CONFIG_MAX_JSON_DEPTH:
        raise ModConfigValidationError((ConfigValidationIssue("json_depth", "$", "exceeds the maximum JSON nesting depth"),))
    if legacy_config.get("config_version") == CURRENT_MOD_CONFIG_VERSION:
        migrated = parse_mod_config(legacy_config)
        if not migrated.get("icon") and (icon := _migrate_icon("", mod_root_path)):
            migrated["icon"] = icon
            return parse_mod_config(migrated)
        return migrated
    if legacy_config.get("config_version") not in (None, "", MOD_CONFIG_VERSION):
        raise ModConfigValidationError((ConfigValidationIssue("config_version", "config_version", "uses an unsupported config version"),))
    if isinstance(legacy_config.get("files"), list):
        raise ModConfigValidationError((ConfigValidationIssue("config_version", "config_version", "operation files require the current config version"),))
    normalized = deepcopy(dict(legacy_config))
    normalize_mod_config_data(normalized, mod_root_path=os.fspath(mod_root_path) if mod_root_path else None)
    game = str(normalized.get("game") or "deltarune").strip().casefold()
    files: list[dict[str, object]] = []
    legacy_files = normalized.get("files")
    if isinstance(legacy_files, Mapping):
        for chapter_id, raw_entry in legacy_files.items():
            if not isinstance(chapter_id, str) or not isinstance(raw_entry, Mapping):
                continue
            data_path = raw_entry.get("data_file_path")
            if isinstance(data_path, str) and data_path.strip():
                files.append({"source": _source_path(data_path), "target": f"{section_target_root(game, chapter_id)}/data.win", "type": "patch"})
            for extra in parse_extra_file_entries_raw(raw_entry.get("extra_files")):
                operation = _extra_operation(
                    game=game, chapter_id=chapter_id, stored_path=extra.get("file_path"),
                    target=extra.get("target"), target_path=extra.get("target_path"),
                    mod_root_path=mod_root_path, directory_paths=legacy_directory_paths,
                )
                if operation is not None:
                    files.append(operation)
    info_files = normalized.get("info_files")
    if isinstance(info_files, Mapping):
        files.extend(
            {"source": _source_path(path), "type": "info"}
            for path, state in info_files.items()
            if (
                state == "show"
                and isinstance(path, str)
                and path.strip()
                and Path(path).suffix.casefold() in MOD_DOCUMENTATION_EXTENSIONS
            )
        )
    legacy_author = normalized.get("author")
    migrated: dict[str, object] = {
        "config_version": CURRENT_MOD_CONFIG_VERSION,
        "id": normalized.get("id") or "legacy_mod",
        "name": normalized.get("name") or "Legacy Mod",
        "version": normalized.get("version") or "1.0.0",
        "authors": [legacy_author]
        if isinstance(legacy_author, str) and legacy_author
        else [],
        "game": game,
        "files": files,
    }
    for field in ("description", "homepage", "game_version", "tags"):
        if (value := normalized.get(field)) not in (None, "", []):
            if isinstance(value, str) and field == "description":
                value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
            elif isinstance(value, str) and field == "game_version":
                value = unicodedata.normalize("NFC", value)[:MOD_CONFIG_MAX_DISPLAY_CHARS].strip()
            migrated[field] = value
    if icon := _migrate_icon(normalized.get("icon"), mod_root_path):
        migrated["icon"] = icon
    return parse_mod_config(migrated)


def migrate_legacy_config_bytes(
    raw: bytes,
    *,
    mod_root_path: str | Path | None = None,
    legacy_directory_paths: Collection[str] = (),
) -> dict[str, object]:
    """Migrate one UTF-8 document without changing its containing file."""
    if len(raw) > MOD_CONFIG_MAX_BYTES:
        raise ModConfigValidationError((ConfigValidationIssue("too_large", "$", "exceeds the 4 MiB size limit"),))
    try:
        legacy = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as error:
        raise ModConfigValidationError((ConfigValidationIssue("invalid_utf8", "$", "must be UTF-8"),)) from error
    except _DuplicateJsonKeyError as error:
        raise ModConfigValidationError((ConfigValidationIssue("duplicate_key", "$", f"duplicates JSON key {error.key!r}"),)) from error
    except json.JSONDecodeError as error:
        raise ModConfigValidationError((ConfigValidationIssue("invalid_json", "$", f"invalid JSON: {error.msg}"),)) from error
    except RecursionError as error:
        raise ModConfigValidationError((ConfigValidationIssue("json_depth", "$", "exceeds the maximum JSON nesting depth"),)) from error
    if not isinstance(legacy, Mapping):
        raise ModConfigValidationError((ConfigValidationIssue("type", "$", "legacy config must be an object"),))
    return migrate_legacy_config(legacy, mod_root_path=mod_root_path, legacy_directory_paths=legacy_directory_paths)


def migrate_legacy_config_file(path: str | Path) -> dict[str, object]:
    """Atomically migrate one G3M-owned config file."""
    config_path = Path(path)
    raw = read_mod_config_bytes(config_path)
    migrated = migrate_legacy_config_bytes(raw, mod_root_path=config_path.parent)
    try:
        original = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError):
        original = None
    if not isinstance(original, Mapping) or original != migrated:
        _write_config_payload(config_path, (json.dumps(migrated, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return migrated
