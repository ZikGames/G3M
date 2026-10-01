"""Centralized user-data migrations and legacy field mapping."""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from config.settings_schema import DEFAULT_APP_SETTINGS

logger = logging.getLogger(__name__)

LEGACY_THEME_COLOR_KEYS = {
    "custom_color_background": "custom_background_color",
    "custom_color_elements": "custom_elements_color",
    "custom_color_border": "custom_border_color",
    "custom_color_hover": "custom_hover_color",
    "custom_color_select": "custom_select_color",
    "custom_color_main_text": "custom_main_text_color",
    "custom_color_secondary_text": "custom_secondary_text_color",
    "custom_color_button": "custom_elements_color",
    "custom_color_button_hover": "custom_hover_color",
    "custom_color_button_select": "custom_select_color",
    "custom_color_text": "custom_main_text_color",
    "custom_color_version_text": "custom_secondary_text_color",
}

LEGACY_CHAPTER_IDS = {
    "-1": "deltarune",
    "0": "deltarune_0",
    "1": "deltarune_1",
    "2": "deltarune_2",
    "3": "deltarune_3",
    "4": "deltarune_4",
    "5": "deltarune_5",
    "-10": "deltarunedemo",
    "-20": "undertale",
    "-30": "undertaleyellow",
    "-40": "pizzatower",
    "-50": "sugaryspire",
}


def migrate_theme_settings(settings: dict[str, Any]) -> bool:
    changed = False
    for legacy_key, current_key in LEGACY_THEME_COLOR_KEYS.items():
        if legacy_key not in settings:
            continue
        legacy_value = settings.pop(legacy_key, "")
        changed = True
        if legacy_value and not settings.get(current_key):
            settings[current_key] = legacy_value
    return changed


def normalize_theme_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of theme settings normalized from legacy keys to current keys."""
    normalized = dict(settings or {})
    migrate_theme_settings(normalized)
    return normalized


def migrate_settings_payload(local_config: dict[str, Any], app_version: str) -> bool:
    changed = False
    if local_config.get("cache_format_version") != app_version:
        local_config["cache_format_version"] = app_version
        changed = True
    if migrate_theme_settings(local_config):
        changed = True
    for key, value in DEFAULT_APP_SETTINGS.items():
        if key not in local_config:
            local_config[key] = value
            changed = True
    return changed


def migrate_legacy_chapter_id(chapter_id: str) -> str:
    return LEGACY_CHAPTER_IDS.get(str(chapter_id), str(chapter_id))


def migrate_profile_settings(
    local_config: dict[str, Any],
    default_profile_path: Path,
    is_profile_key,
    write_profile,
) -> bool:
    if default_profile_path.exists():
        return False
    profile_data = {}
    for key in list(local_config.keys()):
        if is_profile_key(key):
            profile_data[key] = local_config.pop(key)
    if not profile_data:
        return False
    write_profile(profile_data)
    return True


def merge_dicts(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge source dict into destination dict."""
    result = dst.copy()
    for key, value in src.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def merge_json_file(
    source: Path,
    target: Path,
    logger: logging.Logger,
) -> bool:
    try:
        source_data = json.loads(source.read_text("utf-8")) or {}
        target_data = json.loads(target.read_text("utf-8")) or {}
        if isinstance(source_data, dict) and isinstance(target_data, dict):
            merged_data = merge_dicts(target_data, source_data)
            target.write_text(
                json.dumps(merged_data, indent=2, ensure_ascii=False),
                "utf-8",
            )
            source.unlink(missing_ok=True)
            return True
    except Exception as e:
        logger.warning("Profile migration merge failed %s -> %s: %s", source, target, e)
    return False


def migrate_legacy_profile_mods(
    legacy_dir: Path,
    target_dir: Path,
    logger: logging.Logger,
) -> bool:
    if not legacy_dir.exists() or not legacy_dir.is_dir():
        return False
    target_dir.mkdir(parents=True, exist_ok=True)
    migrated = False
    for source in list(legacy_dir.iterdir()):
        target = target_dir / source.name
        try:
            if target.exists():
                if source.name == "mods_data.json":
                    if not merge_json_file(source, target, logger):
                        continue
                    migrated = True
                    continue
                else:
                    base_target = target
                    counter = 1
                    while target.exists():
                        target = target.with_name(f"{base_target.name}_{counter}")
                        counter += 1
                    logger.warning(
                        "Profile migration renamed %s to %s because %s already exists",
                        source,
                        target,
                        base_target,
                    )
            shutil.move(str(source), str(target))
            migrated = True
        except Exception as e:
            logger.error(
                "Profile migration failed %s -> %s: %s",
                source,
                target,
                e,
            )
    with contextlib.suppress(OSError):
        legacy_dir.rmdir()
    return migrated


def find_imported_profile_json(
    directory: Path,
    ignored_names: set[str],
) -> Path | None:
    return next(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".json"
            and path.name.lower() not in ignored_names
        ),
        None,
    )


def migrate_mod_metadata(
    config_data: dict[str, Any],
    mods_metadata: dict[str, dict[str, Any]],
) -> tuple[str | None, bool]:
    mod_id = config_data.get("id")
    if not mod_id:
        return None, False
    if not any(field in config_data for field in ("installed_date", "added_date")):
        return mod_id, False
    mod_metadata = mods_metadata.setdefault(mod_id, {})
    if "installed_date" in config_data:
        mod_metadata["added_date"] = config_data.pop("installed_date")
    elif "added_date" in config_data:
        mod_metadata["added_date"] = config_data.pop("added_date")
    return mod_id, True
