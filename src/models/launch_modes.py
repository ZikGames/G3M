"""User-selectable ways to run the current mod operation."""

from __future__ import annotations

from enum import StrEnum


class LaunchMode(StrEnum):
    NORMAL = "launch"
    KEEP_CHANGES = "launch_keep_changes"
    PATCHING_ONLY = "launch_patching_only"

    @property
    def label_key(self) -> str:
        return f"launch_modes.{self.value}.label"

    @property
    def description_key(self) -> str:
        return f"launch_modes.{self.value}.description"

    @property
    def starts_game(self) -> bool:
        return self is not LaunchMode.PATCHING_ONLY

    @property
    def restores_after_game(self) -> bool:
        return self is LaunchMode.NORMAL

    @property
    def needs_confirmation(self) -> bool:
        return self is not LaunchMode.NORMAL


def get_launch_mode(value: object) -> LaunchMode:
    try:
        return LaunchMode(str(value))
    except ValueError:
        return LaunchMode.NORMAL
