"""Entries exposed by the combined plugins and themes catalog."""

from __future__ import annotations

from dataclasses import dataclass, field

THEME_TAGS = ("minimalist", "game_inspired", "animated", "with_sound")


@dataclass(slots=True)
class CatalogThemeEntry:
    id: str
    name: str
    description: str
    author: str
    version: str
    icon: str = ""
    homepage: str = ""
    download_link: str = ""
    tags: list[str] = field(default_factory=list)
