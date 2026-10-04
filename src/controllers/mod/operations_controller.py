"""Controller for mod installation and operation management."""

import contextlib
import logging
import os

from PyQt6.QtWidgets import QDialog

from adapters.gamebanana_adapter import GameBananaAPI
from config.config import UI_COLORS
from services.localization_service import tr
from ui.dialogs.file_picker_dialog import GameBananaFilePickerDialog
from utils.mod.utils import (
    get_gamebanana_item_type,
    get_gamebanana_mod_id,
    get_mod_id,
    get_mod_name,
    sort_gamebanana_files_by_priority,
)
from utils.process_utils import format_filesystem_error

logger = logging.getLogger(__name__)


class ModOperationsController:
    """Manages mod installation operations and related workflows."""

    def __init__(self, app_state, feedback_service, mod_service, app_window) -> None:
        self.app_state = app_state
        self.feedback_service = feedback_service
        self.mod_service = mod_service
        self.app = app_window
    def _safe_execute(self, func, error_msg_prefix="", default_return=None):
        try:
            return func()
        except (AttributeError, RuntimeError) as e:
            logger.debug(f"{error_msg_prefix}: {e}", exc_info=True)
            return default_return
        except Exception as e:
            logger.debug(f"{error_msg_prefix}: {e}")
            return default_return

    def _safe_update_status(self, message: str, color: str) -> None:
        try:
            self.feedback_service.update_status(message, color)
        except Exception as e:
            logger.warning(
                "ModOperationsController: status update failed: %s",
                e,
                exc_info=True,
            )

    def _safe_show_message(self, *args, **kwargs) -> None:
        try:
            self.feedback_service.show_message(*args, **kwargs)
        except Exception as e:
            logger.warning(
                "ModOperationsController: feedback message failed: %s",
                e,
                exc_info=True,
            )

    def _pick_gamebanana_file(self, available_files, mod_name, homepage):
        available_files = sort_gamebanana_files_by_priority(available_files)
        if len(available_files) <= 1:
            return available_files[0] if available_files else None
        dialog = GameBananaFilePickerDialog(
            self.app, available_files, mod_name, homepage
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._safe_update_status(
                tr("status.operation_cancelled"), UI_COLORS["status_warning"]
            )
            return None
        return dialog.get_selected_file() or available_files[0]

    def _handle_install_start_error(self, error: Exception) -> None:
        self.app_state.is_installing = False
        self.set_install_buttons_enabled(True)
        self.app_state.clear_current_task()
        self._safe_execute(
            lambda: self.app.game_launch.update_button_state(),
            "Failed to update button state",
        )
        self._safe_execute(
            lambda: self.feedback_service.show_message(
                "error", "errors.gamebanana_install_failed", error=str(error)
            ),
            "Failed to show install start error",
        )

    def on_mod_download_requested(self, mod):
        if self.app_state.is_installing:
            logger.debug(
                "ModOperationsController: Installation already in progress, ignoring request"
            )
            return
        if self.app_state.current_task and self.app_state.current_task.isRunning():
            logger.debug(
                "ModOperationsController: Previous task still running, ignoring request"
            )
            return
        self.install_mod(mod)

    def _install_gamebanana_mod(
        self, mod, force=False, is_update=False, selected_file=None
    ):
        try:
            self._enqueue_gamebanana_download(mod, selected_file)
        except Exception as e:
            logger.error(
                f"Error starting GameBanana mod installation: {e}", exc_info=True
            )
            self._handle_install_start_error(e)

    def _enqueue_gamebanana_download(self, mod, selected_file=None):
        """Route a GameBanana mod install through the Downloads system."""
        from models.download_models import SourceKind, TargetKind

        mod_id_str = get_gamebanana_mod_id(mod)
        if not mod_id_str:
            self._safe_show_message(
                "error", "errors.invalid_gamebanana_mod_id"
            )
            return
        mod_id = int(mod_id_str)
        itemtype = get_gamebanana_item_type(mod)
        item_type_lower = "wip" if itemtype == "Wip" else "mod"
        download_url = None
        file_id = None
        file_name = None
        compatibility = None
        size_bytes = None
        md5 = None
        timestamp = None
        if selected_file:
            download_url = selected_file.get("download_url") or selected_file.get(
                "_sDownloadUrl"
            )
            file_id = selected_file.get("id") or selected_file.get("_idRow")
            file_name = selected_file.get("name") or selected_file.get("_sFile")
            compatibility = selected_file.get("compatibility")
            size_bytes = selected_file.get("size_bytes") or selected_file.get("_nFilesize")
            md5 = selected_file.get("md5") or selected_file.get("_sMd5Checksum")
            timestamp = selected_file.get("timestamp") or selected_file.get("_tsDateAdded")
        if not download_url and file_id:
            download_url = f"https://gamebanana.com/dl/{file_id}"
        if not download_url:
            self._safe_show_message("error", "errors.no_download_url")
            return
        canonical_key = (
            f"gb_{item_type_lower}_{mod_id}_{file_id}"
            if file_id
            else f"gb_{item_type_lower}_{mod_id}"
        )
        metadata = {
            "gb_mod_id": mod_id,
            "item_type": item_type_lower,
            "gb_file_id": file_id,
            "file_name": file_name,
            "size_bytes": size_bytes,
            "md5": md5,
            "timestamp": timestamp,
            "compatibility": compatibility,
            "name": getattr(mod, "name", None),
            "authors": getattr(mod, "authors", []) or [],
            "version": (selected_file or {}).get("version") or (selected_file or {}).get("_sVersion") or getattr(mod, "version", None),
            "game_version": getattr(mod, "game_version", None),
            "description": getattr(mod, "description", None),
            "homepage": getattr(mod, "homepage", None),
            "icon": getattr(mod, "icon", None),
            "tags": getattr(mod, "tags", None) or [],
            "category": getattr(mod, "gamebanana_category", None),
            "game": getattr(mod, "game", "deltarune"),
        }
        display_name = get_mod_name(mod, file_name or f"GameBanana mod {mod_id}")
        self.app.downloads_manager.enqueue_with_feedback(
            self.feedback_service,
            display_name=display_name,
            source_kind=SourceKind.GAMEBANANA,
            target_kind=TargetKind.MOD,
            source_url=download_url,
            canonical_key=canonical_key,
            metadata=metadata,
        )
        self._safe_execute(
            lambda: self.app.search_display.update_search_cards(),
            "Failed to refresh cards",
        )

    def enqueue_resolved_gamebanana_update(
        self,
        mod,
        resolved: dict,
        *,
        replace_current: bool,
        batch_id: str,
        mod_folder: str | None = None,
        target_mods_dir: str | None = None,
    ) -> str | None:
        """Queue one already-resolved update, snapshotting it before replacement."""
        from models.download_models import SourceKind, TargetKind

        metadata = resolved.get("metadata")
        source_url = resolved.get("source_url")
        canonical_key = resolved.get("canonical_key")
        if not isinstance(metadata, dict) or not isinstance(source_url, str):
            return None
        mod_id = get_mod_id(mod)
        if not isinstance(mod_id, str):
            return None
        if not replace_current:
            mod_folder = mod_folder or self.mod_service.get_mod_folder_path(mod_id)
            if not mod_folder or not os.path.isdir(mod_folder):
                return None
        update_metadata = {
            **metadata,
            "update_batch_id": batch_id,
            "update_mod_id": mod_id,
            "target_mods_dir": target_mods_dir or self.app_state.mods_dir,
        }
        if not replace_current:
            update_metadata["snapshot_mod_folder"] = mod_folder
            update_metadata["snapshot_version"] = str(getattr(mod, "version", "") or "previous")
        record_id, duplicate = self.app.downloads_manager.enqueue(
            display_name=str(resolved.get("display_name") or getattr(mod, "name", mod_id)),
            source_kind=SourceKind.GAMEBANANA,
            target_kind=TargetKind.MOD,
            source_url=source_url,
            canonical_key=(
                f"{canonical_key}:update:{batch_id}" if canonical_key else None
            ),
            metadata=update_metadata,
            auto_use=True,
        )
        if duplicate:
            self.app.downloads_manager.action_install(record_id)
        return record_id

    def _get_available_gamebanana_files(self, mod) -> list[dict]:
        files = getattr(mod, "gamebanana_supported_files", []) or []
        if files:
            files = sort_gamebanana_files_by_priority(files)
            mod.gamebanana_supported_files = files
            self._notify_gamebanana_card_refresh()
            return files
        mod_id_str = get_gamebanana_mod_id(mod)
        if not mod_id_str:
            return []
        mod_id = int(mod_id_str)
        try:
            api = GameBananaAPI()
            itemtype = get_gamebanana_item_type(mod)
            compat = api.get_supported_files_for_mod(mod_id, itemtype=itemtype)
            files = sort_gamebanana_files_by_priority(
                compat.get("supported_files") or []
            )
            if files:
                mod.gamebanana_supported_files = files
                mod.gamebanana_compatibility_checked = compat.get(
                    "compatibility_checked", False
                )
                self._notify_gamebanana_card_refresh()
            return files
        except Exception as e:
            logger.warning(
                f"ModOperationsController: Failed to refresh GameBanana files for {mod_id}: {e}"
            )
            return []

    def _get_all_gamebanana_files(self, mod) -> list[dict]:
        mod_id_str = get_gamebanana_mod_id(mod)
        if not mod_id_str:
            return []
        mod_id = int(mod_id_str)
        try:
            api = GameBananaAPI()
            itemtype = get_gamebanana_item_type(mod)
            all_files = api.get_mod_files(mod_id, itemtype=itemtype)
            if not all_files:
                return []
            formatted_files = []
            for file_data in all_files:
                file_id = file_data.get("_idRow")
                if not file_id:
                    for key in file_data:
                        if key.isdigit():
                            file_id = int(key)
                            break
                if not file_id:
                    logger.warning(
                        f"ModOperationsController: Could not extract file_id from file_data: {file_data}"
                    )
                    continue
                has_contents = file_data.get("_bHasContents", True)
                if not has_contents:
                    continue
                file_name = (
                    file_data.get("_sFile")
                    or file_data.get("_sName")
                    or file_data.get("name")
                    or f"file_{file_id}"
                )
                download_url = file_data.get("_sDownloadUrl") or file_data.get(
                    "download_url"
                )
                if not download_url:
                    download_url = f"https://gamebanana.com/dl/{file_id}"
                    logger.debug(
                        f"ModOperationsController: Constructed download URL for file {file_id}: {download_url}"
                    )
                formatted_file = {
                    "id": file_id,
                    "name": file_name,
                    "download_url": download_url,
                    "_sDownloadUrl": download_url,
                    "_sFile": file_name,
                    "_idRow": file_id,
                    "_bHasContents": True,
                    "version": file_data.get("_sVersion")
                    or file_data.get("version", ""),
                    "timestamp": file_data.get("_tsDateAdded") or file_data.get("timestamp"),
                    "size_bytes": file_data.get("_nFilesize")
                    or file_data.get("size_bytes", 0),
                    "md5": file_data.get("md5") or file_data.get("_sMd5Checksum"),
                    "download_count": file_data.get("_nDownloadCount")
                    or file_data.get("download_count", 0),
                }
                formatted_files.append(formatted_file)
            return formatted_files
        except Exception as e:
            logger.error(
                f"ModOperationsController: Failed to get all GameBanana files for {mod_id}: {e}",
                exc_info=True,
            )
            return []

    def _notify_gamebanana_card_refresh(self):
        try:
            if hasattr(self.app, "search_display"):
                self.app.search_display.update_search_cards()
        except Exception as e:
            logger.debug("_notify_gamebanana_card_refresh failed", exc_info=e)

    def install_mod(self, mod, force=False, is_update=False):
        try:
            if self.app_state.is_installing and (not force):
                return
            if (
                hasattr(mod, "is_gamebanana_mod")
                and callable(mod.is_gamebanana_mod)
                and mod.is_gamebanana_mod()
            ):
                available_files = self._get_available_gamebanana_files(
                    mod
                ) or self._get_all_gamebanana_files(mod)
                if not available_files:
                    self._safe_show_message(
                        "warning", "errors.mod_no_files", mod_name=mod.name
                    )
                    return
                selected_file = self._pick_gamebanana_file(
                    available_files, mod.name, getattr(mod, "homepage", None)
                )
                if selected_file is None:
                    return
                self._install_gamebanana_mod(
                    mod, force, is_update, selected_file=selected_file
                )
                return
            self._safe_show_message("warning", "errors.mod_no_files", mod_name=mod.name)
        except (OSError, KeyError, Exception) as e:
            logger.error("ModOperationsController: install start failed: %s", e, exc_info=True)
            self._handle_install_start_error(e)

    def on_mod_uninstall_requested(self, mod):
        if self.app_state.is_installing:
            return
        if self.feedback_service.ask_question(
            "dialogs.delete_confirmation",
            "dialogs.delete_mod_confirmation",
            "",
            False,
            mod_name=mod.name,
        ):
            self.uninstall_mod(mod)

    def uninstall_mod(self, mod):
        try:
            self.mod_service.delete_mod_files(mod)
            if used_mods_service := getattr(self.app, "used_mods_service", None):
                used_mods_service.remove_mod_from_all_chapters(mod)
            if hasattr(self.app, "search_display"):
                self.app.search_display.update_search_cards()
                self.app.search_display.update_filtered_mods(preserve_page=True)
            if hasattr(self.app, "library_display"):
                self.app.library_display.update_display()
        except Exception as e:
            logger.error(
                f"ModOperationsController: Failed to uninstall mod: {e}", exc_info=True
            )
            mod_path = ""
            with contextlib.suppress(Exception):
                mod_path = self.mod_service.get_mod_folder_path(get_mod_id(mod)) or ""
            self._safe_show_message(
                "error",
                tr("errors.error"),
                tr(
                    "errors.mod_uninstall_failed",
                    error=format_filesystem_error(e, path=mod_path),
                ),
            )
            return

    def set_install_buttons_enabled(self, enabled: bool):
        button_enabled = self.app_state.is_installing or enabled
        self._safe_execute(
            lambda: self.app.action_button.setEnabled(button_enabled),
            "Failed to set install buttons enabled",
        )
