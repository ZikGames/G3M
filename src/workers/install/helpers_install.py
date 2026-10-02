"""Shared helper functions for mod installation workers."""

import logging
import os
import re
from pathlib import Path

from config.config import MOD_CONFIG_FILENAME
from utils.mod.config import read_mod_config_bytes, write_mod_config
from utils.mod.legacy_config_migration import migrate_legacy_config_bytes

logger = logging.getLogger(__name__)


def find_mod_config(content_path: str) -> str | None:
    """Return the config at an unwrapped package root, if present."""
    config_path = Path(content_path) / MOD_CONFIG_FILENAME
    return str(config_path) if config_path.is_file() and not config_path.is_symlink() else None


def normalize_mod_id(config_data: dict) -> str:
    """Give a newly imported operation config one valid local identifier."""
    candidate = str(config_data.get("id") or config_data.get("name") or "imported_mod")
    normalized = re.sub(r"[^a-z0-9_-]+", "_", candidate.casefold()).strip("_-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"local_{normalized}".rstrip("_")
    mod_id = normalized[:64] or "local_imported_mod"
    config_data["id"] = mod_id
    return mod_id


def load_mod_config(config_path: str) -> dict | None:
    """Parse one package at the import boundary into the current format."""
    try:
        return migrate_legacy_config_bytes(
            read_mod_config_bytes(config_path),
            mod_root_path=os.path.dirname(config_path),
        )
    except Exception as e:
        logger.error(f"Error reading mod config: {e}")
        return None


def save_mod_config(config_path: str, config_data: dict, indent: int = 4):
    """Save mod config to file."""
    try:
        write_mod_config(config_path, config_data, indent=indent)
    except Exception as e:
        logger.error(f"Error writing mod config: {e}")
        raise
