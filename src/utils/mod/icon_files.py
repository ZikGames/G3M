"""Icon files placed into a mod folder while the mod is edited.

Browse copies the picked image into the mod at once and replaces the icon the mod had; the replaced
file is kept aside until Save, so Cancel can put everything back. A path typed by hand is copied
only when the mod is saved.
"""

import filecmp
import logging
import os
import shutil
import tempfile
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _same(first: str, second: str) -> bool:
    """Whether two paths are one file or hold identical bytes."""
    try:
        return _same_file(first, second) or filecmp.cmp(first, second, shallow=False)
    except OSError:
        return False


def _inside(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) == os.path.abspath(root)
    except ValueError:
        return False


class IconFiles:
    """Keeps track of the icon files one editing session has added, replaced or removed."""

    def __init__(self, root: str, original: str | None, is_referenced: Callable[[str], bool]) -> None:
        self.root = root
        self.original = original
        self._is_referenced = is_referenced
        self._staged: str | None = None
        self._stashed: list[tuple[str, str]] = []
        self._stash_dir: str | None = None
        self._pending_overwrite: tuple[str, str] | None = None
        self._replaced_by_save: str | None = None

    def adopt(self, source: str, current: str | None) -> str:
        """Browse: copy ``source`` into the mod now, replacing ``current`` if that is the mod's own icon."""
        if not os.path.isfile(source):
            return source
        if self._staged and _same_file(source, self._staged):
            return os.path.basename(self._staged)
        if current and self._staged and _same_file(current, self._staged):
            current = None
        self._drop_staged()
        if current and self._replaceable(current) and not _same_file(current, source):
            self._stash(current)
        name = self._free_name(source)
        target = os.path.join(self.root, name)
        if os.path.exists(target):
            return name
        try:
            shutil.copy2(source, target)
        except OSError:
            logger.warning("Could not copy icon %s into %s", source, self.root, exc_info=True)
            return source
        self._staged = target
        return name

    def place(self, source: str) -> str | None:
        """Save: make the icon typed by hand live in the mod folder and return its file name."""
        candidate = source if os.path.isabs(source) else os.path.join(self.root, source)
        if not os.path.isfile(candidate):
            return None
        if _inside(self.root, candidate) and os.path.dirname(os.path.abspath(candidate)) == os.path.abspath(self.root):
            self._replaced_by_save = self._replaceable_original(os.path.basename(candidate))
            return os.path.basename(candidate)
        filename = os.path.basename(candidate)
        target = os.path.join(self.root, filename)
        if os.path.exists(target) and not _same(candidate, target):
            if self.original and _same_file(target, self.original) and self._replaceable(target):
                # The mod's own icon is being replaced: write the new one next to it, swap on commit.
                pending = target + ".new"
                shutil.copy2(candidate, pending)
                self._pending_overwrite = (pending, target)
                return filename
            filename = self._free_name(candidate)
            target = os.path.join(self.root, filename)
        if not os.path.exists(target):
            shutil.copy2(candidate, target)
        self._replaced_by_save = self._replaceable_original(filename)
        return filename

    def commit(self, kept_name: str | None) -> None:
        """Called once the config is written: finish replacements and forget the backups."""
        try:
            if self._pending_overwrite:
                pending, target = self._pending_overwrite
                os.replace(pending, target)
        except OSError:
            logger.warning("Could not replace the mod icon", exc_info=True)
        if self._replaced_by_save:
            self._remove(self._replaced_by_save)
        if self._staged and os.path.basename(self._staged) != kept_name:
            self._remove(self._staged)
        self._forget()

    def rollback(self) -> None:
        """Cancel: remove what was added and put the replaced icon back."""
        self._drop_staged()
        for original, backup in self._stashed:
            try:
                shutil.copy2(backup, original)
            except OSError:
                logger.warning("Could not restore icon %s", original, exc_info=True)
        if self._pending_overwrite:
            self._remove(self._pending_overwrite[0])
        self._forget()

    def _replaceable(self, path: str) -> bool:
        return os.path.isfile(path) and _inside(self.root, path) and not self._is_referenced(path)

    def _replaceable_original(self, final_name: str) -> str | None:
        """The mod's previous icon, when saving a different one should remove it."""
        original = self.original
        if original and os.path.isfile(original) and os.path.basename(original) != final_name and self._replaceable(original):
            return original
        return None

    def _free_name(self, source: str) -> str:
        stem, extension = os.path.splitext(os.path.basename(source))
        name, counter = f"{stem}{extension}", 1
        while os.path.exists(target := os.path.join(self.root, name)) and not _same(source, target):
            name, counter = f"{stem}_{counter}{extension}", counter + 1
        return name

    def _stash(self, path: str) -> None:
        if any(original == path for original, _ in self._stashed):
            return
        if self._stash_dir is None:
            self._stash_dir = tempfile.mkdtemp(prefix="g3m_icon_backup_")
        backup = os.path.join(self._stash_dir, f"{len(self._stashed)}_{os.path.basename(path)}")
        try:
            shutil.copy2(path, backup)
            os.remove(path)
        except OSError:
            logger.warning("Could not set aside icon %s", path, exc_info=True)
            return
        self._stashed.append((path, backup))

    def _drop_staged(self) -> None:
        staged, self._staged = self._staged, None
        if staged:
            self._remove(staged)

    @staticmethod
    def _remove(path: str) -> None:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            logger.warning("Could not remove icon file %s", path, exc_info=True)

    def _forget(self) -> None:
        if self._stash_dir:
            shutil.rmtree(self._stash_dir, ignore_errors=True)
        self._staged = None
        self._stashed.clear()
        self._stash_dir = None
        self._pending_overwrite = None
        self._replaced_by_save = None
