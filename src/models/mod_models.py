"""Models for installed current configs and remote mod metadata."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, cast


def _metadata_value[T](data: dict[str, Any], key: str, default: T) -> T:
    value = data.get(key)
    if value not in (None, "", [], {}):
        return cast(T, value)
    metadata = data.get("metadata")
    value = metadata.get(key) if isinstance(metadata, dict) else None
    return cast(T, value) if value not in (None, "", [], {}) else default


def get_mod_authors(data: dict[str, Any], default: str = "") -> list[str]:
    """Return the current config's canonical author list."""
    authors = _metadata_value(data, "authors", [])
    if isinstance(authors, list):
        names = [name.strip() for name in authors if isinstance(name, str) and name.strip()]
        if names:
            return names
    return [default] if default else []


def format_mod_authors(authors: Iterable[object]) -> str:
    """Format canonical author names for display."""
    return ", ".join(
        name.strip() for name in authors if isinstance(name, str) and name.strip()
    )


def _config_sections(data: dict[str, Any]) -> frozenset[str]:
    from utils.mod.config import MOD_CONFIG_VERSION, config_has_files_for_section

    if data.get("config_version") != MOD_CONFIG_VERSION:
        return frozenset()
    from models.game_modes import get_game

    game = str(data.get("game") or "")
    definition = get_game(game)
    section_ids = [tab.tab_id for tab in definition.tabs] if definition else [game]
    return frozenset(
        section_id
        for section_id in section_ids
        if section_id and config_has_files_for_section(data, section_id)
    )


@dataclass
class BaseModInfo:
    """Shared visible metadata; installed content is described only by its config."""

    id: str
    name: str
    version: str
    authors: list[str]
    description: str
    game: str
    game_version: str = ""
    icon: str | None = None
    tags: list[str] = field(default_factory=list)
    homepage: str | None = None
    sections: frozenset[str] | None = None
    playtime_hours: float = 0.0

    @classmethod
    def _common_fields(cls, data: dict[str, Any], *, remote: bool) -> dict[str, Any]:
        from services.localization_service import tr

        return {
            "id": _metadata_value(data, "id", ""),
            "name": _metadata_value(data, "name", "Unknown Mod"),
            "version": _metadata_value(data, "version", "1.0.0"),
            "authors": get_mod_authors(data, tr("defaults.unknown")),
            "description": _metadata_value(data, "description", tr("status.no_description_status")),
            "game": _metadata_value(data, "game", "deltarune"),
            "game_version": _metadata_value(data, "game_version", tr("defaults.not_specified")),
            "icon": _metadata_value(data, "icon", None),
            "tags": _metadata_value(data, "tags", []),
            "homepage": _metadata_value(data, "homepage", None),
            "sections": None if remote else _config_sections(data),
        }

    def supports_section(self, section_id: str) -> bool:
        """Remote listings are not constrained until their package is imported."""
        return self.sections is None or section_id in self.sections

    def is_gamebanana_mod(self) -> bool:
        return self.id.startswith(("gb_mod_", "gb_wip_"))

    def get_gamebanana_mod_id(self) -> str | None:
        from utils.mod.utils import parse_gamebanana_mod_id

        return parse_gamebanana_mod_id(self.id)[1]


@dataclass
class LocalModInfo(BaseModInfo):
    added_date: str | None = None
    last_updated: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalModInfo:
        return cls(
            **cls._common_fields(data, remote=False),
            playtime_hours=data.get("playtime_hours", 0.0),
            added_date=data.get("added_date"),
            last_updated=data.get("last_updated"),
        )


@dataclass
class BrowserModInfo(BaseModInfo):
    description_url: str = ""
    downloads: int | None = None
    like_count: int | None = None
    hide_mod: bool = False
    ban_status: bool = False
    is_nsfw: bool = False
    has_files: bool = True
    is_wip: bool = False
    demo_url: str | None = None
    demo_version: str | None = None
    created_date: str | None = None
    last_updated: str | None = None
    screenshots_url: list[str] = field(default_factory=list)
    full_description: str | None = None
    gamebanana_category: str | None = None
    gamebanana_supported_files: list[dict[str, Any]] = field(default_factory=list)
    gamebanana_compatibility_checked: bool = False
    has_full_metadata: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrowserModInfo:
        return cls(
            **cls._common_fields(data, remote=True),
            description_url=_metadata_value(data, "description_url", ""),
            downloads=data.get("downloads"),
            like_count=data.get("like_count"),
            hide_mod=data.get("hide_mod", False),
            ban_status=data.get("ban_status", False),
            is_nsfw=data.get("is_nsfw", False),
            has_files=data.get("has_files", True),
            is_wip=data.get("is_wip", False),
            demo_url=data.get("demo_url"),
            demo_version=data.get("demo_version"),
            created_date=data.get("created_date"),
            last_updated=data.get("last_updated"),
            screenshots_url=data.get("screenshots_url", []),
            full_description=data.get("full_description"),
            gamebanana_category=data.get("gamebanana_category"),
            gamebanana_supported_files=data.get("gamebanana_supported_files", []),
            gamebanana_compatibility_checked=data.get("gamebanana_compatibility_checked", False),
            has_full_metadata=data.get("has_full_metadata", False),
        )


type AnyModInfo = LocalModInfo | BrowserModInfo
ModInfo = BrowserModInfo
