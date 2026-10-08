"""Backup management for mod installation and restoration."""

import logging
import os
import shutil
import tempfile

from utils.file_utils import safe_remove, safe_rmtree


class BackupManager:
    """Manages file and directory backups for safe mod operations."""

    def __init__(self, backup_dir: str, patching_logger=None) -> None:
        self.backup_dir = backup_dir
        self.patching_logger = patching_logger or logging.getLogger(__name__)
        self.original_files: dict[str, dict[str, str | None]] = {}
        if backup_dir:
            os.makedirs(backup_dir, exist_ok=True)

    def backup_file(self, chapter_id: str, file_path: str) -> bool:
        self.original_files.setdefault(chapter_id, {})
        if file_path in self.original_files[chapter_id]:
            return True
        if not os.path.exists(file_path):
            self.original_files[chapter_id][file_path] = None
            self.patching_logger.debug(
                f"[BACKUP] File does not exist, will be removed on restore: {file_path} (chapter {chapter_id})"
            )
            return True
        try:
            backup_filename = os.path.basename(file_path)
            backup_path = os.path.join(
                self.backup_dir, f"chapter_{chapter_id}_{backup_filename}"
            )
            counter = 1
            while os.path.exists(backup_path):
                name, ext = os.path.splitext(backup_filename)
                backup_path = os.path.join(
                    self.backup_dir, f"chapter_{chapter_id}_{name}_{counter}{ext}"
                )
                counter += 1
            shutil.copy2(file_path, backup_path)
            self.original_files[chapter_id][file_path] = backup_path
            self.patching_logger.info(
                f"[BACKUP] Backed up file: {file_path} -> {backup_path} (chapter {chapter_id})"
            )
            return True
        except Exception as e:
            self.patching_logger.error(
                f"[BACKUP] Failed to backup file {file_path} (chapter {chapter_id}): {e}",
                exc_info=True,
            )
            return False

    def clear_backup_dir(self):
        """Remove the backup directory and clear tracked files."""
        if self.backup_dir and os.path.isdir(self.backup_dir):
            safe_rmtree(self.backup_dir)
            self.patching_logger.info(
                f"[BACKUP] Cleared backup directory: {self.backup_dir}"
            )
        self.original_files.clear()

    def restore_backups(self, chapter_id: str) -> bool:
        success = True
        if chapter_id in self.original_files:
            self.patching_logger.info(
                f"[RESTORE] Restoring backups for chapter {chapter_id}"
            )
            restored_files = []
            failed_files = []
            for file_path, backup_path in reversed(self.original_files[chapter_id].items()):
                if backup_path is None:
                    if os.path.exists(file_path):
                        if safe_remove(file_path):
                            self.patching_logger.info(
                                f"[RESTORE] Removed file created by mod: {file_path} (chapter {chapter_id})"
                            )
                            restored_files.append(file_path)
                        else:
                            self.patching_logger.error(
                                f"[RESTORE] Failed to remove file created by mod {file_path} (chapter {chapter_id})"
                            )
                            failed_files.append(file_path)
                            success = False
                    else:
                        self.patching_logger.debug(
                            f"[RESTORE] File created by mod already removed: {file_path} (chapter {chapter_id})"
                        )
                        restored_files.append(file_path)
                    continue
                if not os.path.exists(backup_path):
                    self.patching_logger.warning(
                        f"[RESTORE] Backup file not found: {backup_path} (original: {file_path}, chapter {chapter_id})"
                    )
                    failed_files.append(file_path)
                    success = False
                    continue
                try:
                    target_dir = os.path.dirname(file_path)
                    if target_dir and (not os.path.exists(target_dir)):
                        os.makedirs(target_dir, exist_ok=True)
                        self.patching_logger.debug(
                            f"[RESTORE] Created target directory: {target_dir}"
                        )
                    descriptor, temporary_path = tempfile.mkstemp(
                        prefix=f".{os.path.basename(file_path)}.",
                        suffix=".g3m-restore",
                        dir=target_dir or ".",
                    )
                    os.close(descriptor)
                    try:
                        shutil.copy2(backup_path, temporary_path)
                        os.replace(temporary_path, file_path)
                        temporary_path = ""
                    finally:
                        if temporary_path:
                            safe_remove(temporary_path)
                    if os.path.exists(file_path):
                        backup_size = os.path.getsize(backup_path)
                        restored_size = os.path.getsize(file_path)
                        if backup_size == restored_size:
                            self.patching_logger.info(
                                f"[RESTORE] Restored backup: {file_path} <- {backup_path} (chapter {chapter_id}, size: {restored_size} bytes)"
                            )
                            restored_files.append(file_path)
                        else:
                            self.patching_logger.error(
                                f"[RESTORE] File size mismatch after restoration: {file_path} (backup: {backup_size} bytes, restored: {restored_size} bytes, chapter {chapter_id})"
                            )
                            failed_files.append(file_path)
                            success = False
                    else:
                        self.patching_logger.error(
                            f"[RESTORE] File does not exist after restoration attempt: {file_path} (chapter {chapter_id})"
                        )
                        failed_files.append(file_path)
                        success = False
                except Exception as e:
                    self.patching_logger.error(
                        f"[RESTORE] Failed to restore backup {backup_path} to {file_path} (chapter {chapter_id}): {e}",
                        exc_info=True,
                    )
                    failed_files.append(file_path)
                    success = False
            if failed_files:
                self.patching_logger.warning(
                    f"[RESTORE] Restoration completed with {len(failed_files)} failure(s) for chapter {chapter_id}: {failed_files}"
                )
            else:
                self.patching_logger.info(
                    f"[RESTORE] Successfully restored {len(restored_files)} file(s) for chapter {chapter_id}"
                )
        return success
