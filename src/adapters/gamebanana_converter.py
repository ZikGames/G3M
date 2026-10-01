"""GameBanana mod format conversion."""

import json
import logging
import os
import shutil
import tempfile
from typing import Any

from utils.file_utils import (
    check_filename_is_deltamod_info,
    find_deltamod_info_file,
    flatten_single_child_directories,
    normalize_mod_package,
)
from utils.mod.config import (
    MOD_CONFIG_TAGS,
    parse_mod_config,
    write_mod_config,
)

logger = logging.getLogger(__name__)


class GameBananaConverter:
    """Converts GameBanana mod archives to G3M format."""

    def __init__(
        self,
        archive_path: str,
        mods_dir: str,
        gamebanana_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.archive_path = archive_path
        self.mods_dir = mods_dir
        self.gamebanana_metadata = gamebanana_metadata or {}
        self.temp_extract_dir: str | None = None
        self._previous_mod_dir: str | None = None
        self._previous_mod_backup: str | None = None
        self._converted_mod_dir: str | None = None

    def _cleanup_temp_dir(self) -> None:
        if self.temp_extract_dir and os.path.exists(self.temp_extract_dir):
            try:
                shutil.rmtree(self.temp_extract_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory: {e}")

    def convert(self) -> str | None:
        self._converted_mod_dir = None
        try:
            self.temp_extract_dir = tempfile.mkdtemp(prefix="gb_convert_")
            if not self._check_compatibility():
                logger.debug(
                    f"Archive {self.archive_path} does not contain deltamod info file"
                )
                return None
            self._extract_archive()
            normalize_mod_package(self.temp_extract_dir, require_manifest=True)
            target_mod_id = None
            if self.gamebanana_metadata.get("mod_id"):
                item_type = "wip" if str(self.gamebanana_metadata.get("item_type", "mod")).strip().casefold() == "wip" else "mod"
                target_mod_id = f"gb_{item_type}_{self.gamebanana_metadata['mod_id']}"
            if target_mod_id:
                self._update_deltamod_info_mod_id(target_mod_id)
                self._stage_existing_mod_folder(target_mod_id)
            from adapters.deltamod_adapter import DeltamodConverter

            deltamod_converter = DeltamodConverter(
                self.temp_extract_dir, self.mods_dir, self.gamebanana_metadata
            )
            result_path = deltamod_converter.convert()
            if result_path:
                self._converted_mod_dir = result_path
                result_path = self._update_config_with_gb_metadata(result_path)
                self._restore_versions(result_path)
                self._discard_backup()
            else:
                self._restore_previous_mod()
            return result_path
        except Exception as e:
            logger.error(f"GameBanana conversion failed: {e}", exc_info=True)
            self._remove_failed_conversion()
            self._restore_previous_mod()
            return None
        finally:
            if self._previous_mod_backup is None:
                self._discard_backup()
            self._cleanup_temp_dir()

    def _check_compatibility(self) -> bool:
        try:
            if not os.path.exists(self.archive_path):
                return False
            from utils.mod.archive import list_archive_members

            return any(
                check_filename_is_deltamod_info(os.path.basename(member.name))
                for member in list_archive_members(self.archive_path)
            )
        except Exception as e:
            logger.error(f"Error checking archive compatibility: {e}")
            return False

    def _extract_archive(self) -> None:
        try:
            from utils.archive_utils import extract_any_archive

            temp_extract_dir = self.temp_extract_dir
            if temp_extract_dir is None:
                raise RuntimeError("Temporary extraction directory is not initialized")
            extract_any_archive(self.archive_path, temp_extract_dir)
            flatten_single_child_directories(temp_extract_dir)
        except Exception as e:
            logger.error(f"Error extracting archive: {e}")
            raise

    def _update_deltamod_info_mod_id(self, target_mod_id: str) -> None:
        if self.temp_extract_dir is None:
            return
        deltamod_info_path = find_deltamod_info_file(self.temp_extract_dir)
        if not deltamod_info_path:
            return
        if deltamod_info_path.lower().endswith(".toml"):
            return
        try:
            with open(deltamod_info_path, encoding="utf-8") as f:
                deltamod_info = json.load(f)
            package_id = target_mod_id.replace("_", ".")
            if "metadata" not in deltamod_info:
                deltamod_info["metadata"] = {}
            deltamod_info["metadata"]["packageID"] = package_id
            with open(deltamod_info_path, "w", encoding="utf-8") as f:
                json.dump(deltamod_info, f, indent=4, ensure_ascii=False)
            logger.info(
                f"GameBananaConverter: Updated packageID in deltamod info file to {package_id} (target_mod_id: {target_mod_id})"
            )
        except Exception as e:
            logger.warning(f"Failed to update packageID in deltamod info file: {e}")

    def _stage_existing_mod_folder(self, mod_id: str) -> None:
        if not os.path.exists(self.mods_dir):
            return
        try:
            for folder_name in os.listdir(self.mods_dir):
                folder_path = os.path.join(self.mods_dir, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                config_path = os.path.join(folder_path, "mod_config.json")
                if not os.path.exists(config_path):
                    continue
                try:
                    with open(config_path, encoding="utf-8") as f:
                        config_data = json.load(f)
                    if config_data.get("id") == mod_id:
                        logger.info(
                            "GameBananaConverter: staging existing mod folder %s with id %s",
                            folder_path,
                            mod_id,
                        )
                        backup_root = tempfile.mkdtemp(
                            prefix=".g3m-update-", dir=self.mods_dir
                        )
                        backup_path = os.path.join(backup_root, "previous")
                        shutil.move(folder_path, backup_path)
                        self._previous_mod_dir = folder_path
                        self._previous_mod_backup = backup_path
                        break
                except Exception as e:
                    logger.debug(
                        f"GameBananaConverter: Error checking config in {folder_path}: {e}"
                    )
                    continue
        except Exception as e:
            logger.warning(
                f"GameBananaConverter: Error checking for existing mod folder: {e}"
            )

    def _restore_versions(self, mod_dir: str) -> None:
        if not self._previous_mod_backup:
            return
        versions_dir = os.path.join(self._previous_mod_backup, "mod_versions")
        if not os.path.isdir(versions_dir):
            return
        try:
            shutil.copytree(
                versions_dir,
                os.path.join(mod_dir, "mod_versions"),
                dirs_exist_ok=True,
            )
        except OSError as error:
            logger.error("GameBananaConverter: failed to preserve mod versions: %s", error)
            raise

    def _restore_previous_mod(self) -> bool:
        if not self._previous_mod_dir or not self._previous_mod_backup:
            return True
        try:
            if os.path.exists(self._previous_mod_dir):
                shutil.rmtree(self._previous_mod_dir)
            if os.path.exists(self._previous_mod_backup):
                shutil.move(self._previous_mod_backup, self._previous_mod_dir)
            self._discard_backup()
            return True
        except OSError as error:
            logger.critical(
                "GameBananaConverter: could not restore previous mod at %s: %s",
                self._previous_mod_dir,
                error,
            )
            return False

    def _remove_failed_conversion(self) -> None:
        """Remove a newly created replacement before restoring an old mod."""
        converted = self._converted_mod_dir
        if not converted or not os.path.isdir(converted):
            return
        if self._previous_mod_dir and os.path.normcase(os.path.abspath(converted)) == os.path.normcase(os.path.abspath(self._previous_mod_dir)):
            return
        try:
            shutil.rmtree(converted)
        except OSError:
            logger.warning("GameBananaConverter: failed to remove incomplete replacement %s", converted)

    def _discard_backup(self) -> None:
        if self._previous_mod_backup:
            shutil.rmtree(os.path.dirname(self._previous_mod_backup), ignore_errors=True)
        self._previous_mod_dir = None
        self._previous_mod_backup = None

    def _update_config_with_gb_metadata(self, mod_dir: str) -> str:
        config_path = os.path.join(mod_dir, "mod_config.json")
        if not os.path.exists(config_path):
            logger.warning(
                f"GameBananaConverter: Config file not found at {config_path}"
            )
            raise FileNotFoundError(config_path)
        try:
            with open(config_path, encoding="utf-8") as f:
                config_data = json.load(f)
            if not isinstance(config_data, dict):
                logger.warning(
                    f"GameBananaConverter: Config data is not a dict at {config_path}"
                )
                raise ValueError(f"config data is not an object: {config_path}")
            config_data = parse_mod_config(config_data)
            if self.gamebanana_metadata.get("mod_id"):
                mod_id = str(self.gamebanana_metadata["mod_id"])
                item_type = (
                    "wip"
                    if str(self.gamebanana_metadata.get("item_type", "mod")).strip().casefold()
                    == "wip"
                    else "mod"
                )
                expected_mod_id = f"gb_{item_type}_{mod_id}"
                config_data["id"] = expected_mod_id
                logger.info(
                    f"GameBananaConverter: Updated config - id={expected_mod_id}, mod_dir={mod_dir} (folder name based on mod name)"
                )
            if self.gamebanana_metadata.get("version"):
                config_data["version"] = str(self.gamebanana_metadata["version"])
            if not config_data.get("homepage"):
                homepage = self.gamebanana_metadata.get("homepage") or self.gamebanana_metadata.get(
                    "profile_url"
                )
                if homepage:
                    config_data["homepage"] = homepage
            from adapters.gamebanana_adapter import GameBananaAPI

            tags = []
            if self.gamebanana_metadata.get("tags"):
                tags = self.gamebanana_metadata["tags"]
                if not isinstance(tags, list):
                    tags = [tags] if tags else []
            category_tag = GameBananaAPI.category_to_tag(
                self.gamebanana_metadata.get("category")
            )
            if category_tag and category_tag not in tags:
                tags.append(category_tag)
            if tags:
                existing_tags = config_data.get("tags", [])
                if not isinstance(existing_tags, list):
                    existing_tags = [existing_tags] if existing_tags else []
                for tag in tags:
                    if tag in MOD_CONFIG_TAGS and tag not in existing_tags:
                        existing_tags.append(tag)
                config_data["tags"] = existing_tags
            write_mod_config(config_path, config_data)
            logger.info(
                f"GameBananaConverter: Updated config for GameBanana mod: id={config_data.get('id')}, mod_dir={mod_dir}"
            )
            return mod_dir
        except (OSError, json.JSONDecodeError, TypeError, KeyError) as e:
            logger.error(
                f"GameBananaConverter: Failed to update config with GameBanana metadata: {e}",
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(
                f"GameBananaConverter: Unexpected error updating config: {e}",
                exc_info=True,
            )
            raise
